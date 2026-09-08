from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import logging
from typing import Any
from uuid import UUID

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.utils.quantity import parse_quantity


LOGGER = logging.getLogger(__name__)
MIB = Decimal(1024 * 1024)


class ObservationCollectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ResourceVector:
    cpu_cores: Decimal = Decimal(0)
    memory_bytes: Decimal = Decimal(0)
    ephemeral_storage_bytes: Decimal = Decimal(0)

    def __add__(self, other: "_ResourceVector") -> "_ResourceVector":
        return _ResourceVector(
            cpu_cores=self.cpu_cores + other.cpu_cores,
            memory_bytes=self.memory_bytes + other.memory_bytes,
            ephemeral_storage_bytes=(
                self.ephemeral_storage_bytes + other.ephemeral_storage_bytes
            ),
        )

    def maximum(self, other: "_ResourceVector") -> "_ResourceVector":
        return _ResourceVector(
            cpu_cores=max(self.cpu_cores, other.cpu_cores),
            memory_bytes=max(self.memory_bytes, other.memory_bytes),
            ephemeral_storage_bytes=max(
                self.ephemeral_storage_bytes,
                other.ephemeral_storage_bytes,
            ),
        )

    def as_capacity(self, *, round_up: bool) -> dict[str, int]:
        rounding = ROUND_CEILING if round_up else ROUND_FLOOR
        return {
            "cpu_millicores": int(
                (self.cpu_cores * Decimal(1000)).to_integral_value(
                    rounding=rounding
                )
            ),
            "memory_mib": int(
                (self.memory_bytes / MIB).to_integral_value(rounding=rounding)
            ),
            "ephemeral_storage_mib": int(
                (self.ephemeral_storage_bytes / MIB).to_integral_value(
                    rounding=rounding
                )
            ),
        }


@dataclass(frozen=True, slots=True)
class _ContainerMetrics:
    cpu_usage_millicores: int | None
    memory_usage_mib: int | None
    storage_usage_mib: int | None


def _decimal_quantity(resources: Any, resource_name: str) -> Decimal:
    if resources is None:
        return Decimal(0)
    if isinstance(resources, dict):
        raw_value = resources.get(resource_name)
    else:
        raw_value = getattr(resources, resource_name.replace("-", "_"), None)
    if raw_value is None:
        return Decimal(0)
    try:
        return Decimal(parse_quantity(str(raw_value)))
    except (ValueError, TypeError) as error:
        raise ObservationCollectionError(
            f"Kubernetes returned an invalid {resource_name} quantity"
        ) from error


def _requests_vector(resources: Any) -> _ResourceVector:
    requests = (
        resources.get("requests")
        if isinstance(resources, dict)
        else getattr(resources, "requests", None)
    )
    return _ResourceVector(
        cpu_cores=_decimal_quantity(requests, "cpu"),
        memory_bytes=_decimal_quantity(requests, "memory"),
        ephemeral_storage_bytes=_decimal_quantity(
            requests,
            "ephemeral-storage",
        ),
    )


def _resource_list_vector(resources: Any) -> _ResourceVector:
    return _ResourceVector(
        cpu_cores=_decimal_quantity(resources, "cpu"),
        memory_bytes=_decimal_quantity(resources, "memory"),
        ephemeral_storage_bytes=_decimal_quantity(
            resources,
            "ephemeral-storage",
        ),
    )


def _status_by_name(statuses: Any) -> dict[str, Any]:
    return {
        status.name: status
        for status in (statuses or [])
        if getattr(status, "name", None)
    }


def _container_requests(container: Any, status: Any | None) -> _ResourceVector:
    requested = _requests_vector(getattr(container, "resources", None))
    allocated = _resource_list_vector(
        getattr(status, "allocated_resources", None)
    )
    return requested.maximum(allocated)


def _pod_effective_requests(pod: Any) -> _ResourceVector:
    pod_status = getattr(pod, "status", None)
    phase = (getattr(pod_status, "phase", "") or "").upper()
    if phase in {"SUCCEEDED", "FAILED"}:
        return _ResourceVector()

    pod_spec = getattr(pod, "spec", None)
    regular_statuses = _status_by_name(
        getattr(pod_status, "container_statuses", None)
    )
    init_statuses = _status_by_name(
        getattr(pod_status, "init_container_statuses", None)
    )

    regular_sum = _ResourceVector()
    for container in getattr(pod_spec, "containers", None) or []:
        regular_sum += _container_requests(
            container,
            regular_statuses.get(container.name),
        )

    restartable_init_sum = _ResourceVector()
    initialization_peak = _ResourceVector()
    for init_container in getattr(pod_spec, "init_containers", None) or []:
        request = _container_requests(
            init_container,
            init_statuses.get(init_container.name),
        )
        if getattr(init_container, "restart_policy", None) == "Always":
            restartable_init_sum += request
            initialization_peak = initialization_peak.maximum(
                restartable_init_sum
            )
        else:
            initialization_peak = initialization_peak.maximum(
                restartable_init_sum + request
            )

    effective = (regular_sum + restartable_init_sum).maximum(
        initialization_peak
    )

    pod_level_resources = getattr(pod_spec, "resources", None)
    if pod_level_resources is not None:
        effective = effective.maximum(_requests_vector(pod_level_resources))

    overhead = _resource_list_vector(getattr(pod_spec, "overhead", None))
    return effective + overhead


def _allocated_requests(pods: list[Any]) -> _ResourceVector:
    total = _ResourceVector()
    for pod in pods:
        total += _pod_effective_requests(pod)
    return total


def _node_allocatable(node: Any) -> _ResourceVector:
    status = getattr(node, "status", None)
    allocatable = getattr(status, "allocatable", None)
    if not allocatable:
        raise ObservationCollectionError(
            "Kubernetes node status does not contain allocatable capacity"
        )
    return _resource_list_vector(allocatable)


def _node_ready(node: Any) -> bool:
    conditions = getattr(getattr(node, "status", None), "conditions", None) or []
    return any(
        condition.type == "Ready" and condition.status == "True"
        for condition in conditions
    )


def _non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _usage_millicores(cpu: Any) -> int | None:
    if not isinstance(cpu, dict):
        return None
    usage_nanocores = _non_negative_int(cpu.get("usageNanoCores"))
    return (
        usage_nanocores // 1_000_000
        if usage_nanocores is not None
        else None
    )


def _usage_mib(memory: Any) -> int | None:
    if not isinstance(memory, dict):
        return None
    working_set_bytes = _non_negative_int(memory.get("workingSetBytes"))
    return (
        working_set_bytes // (1024 * 1024)
        if working_set_bytes is not None
        else None
    )


def _storage_usage_mib(container_summary: dict[str, Any]) -> int | None:
    values = []
    for key in ("rootfs", "logs"):
        section = container_summary.get(key)
        if isinstance(section, dict):
            used_bytes = _non_negative_int(section.get("usedBytes"))
            if used_bytes is not None:
                values.append(used_bytes)
    return sum(values) // (1024 * 1024) if values else None


def _node_usage(summary: dict[str, Any]) -> dict[str, int | None]:
    node_summary = summary.get("node")
    if not isinstance(node_summary, dict):
        return {
            "cpu_millicores": None,
            "memory_mib": None,
        }
    return {
        "cpu_millicores": _usage_millicores(node_summary.get("cpu")),
        "memory_mib": _usage_mib(node_summary.get("memory")),
    }


def _summary_metric_indexes(
    summary: dict[str, Any],
) -> tuple[
    dict[tuple[str, str], _ContainerMetrics],
    dict[tuple[str, str, str], _ContainerMetrics],
]:
    by_uid: dict[tuple[str, str], _ContainerMetrics] = {}
    by_name: dict[tuple[str, str, str], _ContainerMetrics] = {}
    for pod_summary in summary.get("pods", []) or []:
        if not isinstance(pod_summary, dict):
            continue
        pod_ref = pod_summary.get("podRef") or {}
        if not isinstance(pod_ref, dict):
            continue
        pod_uid = str(pod_ref.get("uid") or "")
        namespace = str(pod_ref.get("namespace") or "")
        pod_name = str(pod_ref.get("name") or "")
        for container_summary in pod_summary.get("containers", []) or []:
            if not isinstance(container_summary, dict):
                continue
            container_name = str(container_summary.get("name") or "")
            if not container_name:
                continue
            metrics = _ContainerMetrics(
                cpu_usage_millicores=_usage_millicores(
                    container_summary.get("cpu")
                ),
                memory_usage_mib=_usage_mib(
                    container_summary.get("memory")
                ),
                storage_usage_mib=_storage_usage_mib(container_summary),
            )
            if pod_uid:
                by_uid[(pod_uid, container_name)] = metrics
            if namespace and pod_name:
                by_name[(namespace, pod_name, container_name)] = metrics
    return by_uid, by_name


def _container_state(status: Any) -> str:
    state = getattr(status, "state", None)
    if getattr(state, "running", None) is not None:
        return "RUNNING"
    if getattr(state, "waiting", None) is not None:
        return "WAITING"
    if getattr(state, "terminated", None) is not None:
        return "TERMINATED"
    return "UNKNOWN"


def _container_observations(
    pods: list[Any],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    metrics_by_uid, metrics_by_name = _summary_metric_indexes(summary)
    observations: dict[str, dict[str, Any]] = {}

    for pod in pods:
        metadata = getattr(pod, "metadata", None)
        pod_spec = getattr(pod, "spec", None)
        pod_status = getattr(pod, "status", None)
        pod_uid = str(getattr(metadata, "uid", "") or "")
        namespace = str(getattr(metadata, "namespace", "") or "")
        pod_name = str(getattr(metadata, "name", "") or "")
        status_groups = (
            (
                getattr(pod_status, "init_container_statuses", None),
                getattr(pod_spec, "init_containers", None),
            ),
            (
                getattr(pod_status, "container_statuses", None),
                getattr(pod_spec, "containers", None),
            ),
            (
                getattr(pod_status, "ephemeral_container_statuses", None),
                getattr(pod_spec, "ephemeral_containers", None),
            ),
        )
        pod_phase = (getattr(pod_status, "phase", "") or "").upper()
        pod_is_completed = pod_phase in {"SUCCEEDED", "FAILED"}
        for statuses, container_specs in status_groups:
            specs_by_name = {
                container.name: container
                for container in (container_specs or [])
                if getattr(container, "name", None)
            }
            for status in statuses or []:
                container_id = str(getattr(status, "container_id", "") or "")
                container_name = str(getattr(status, "name", "") or "")
                if not container_id or not container_name:
                    continue
                requests = (
                    _ResourceVector()
                    if pod_is_completed
                    else _container_requests(
                        specs_by_name.get(container_name),
                        status,
                    )
                ).as_capacity(round_up=True)
                metrics = metrics_by_uid.get((pod_uid, container_name))
                if metrics is None:
                    metrics = metrics_by_name.get(
                        (namespace, pod_name, container_name)
                    )
                observations[container_id] = {
                    "container_id": container_id,
                    "container_name": container_name,
                    "pod_name": pod_name or None,
                    "namespace": namespace or None,
                    "status": _container_state(status),
                    "cpu_request_millicores": requests["cpu_millicores"],
                    "memory_request_mib": requests["memory_mib"],
                    "ephemeral_storage_request_mib": requests[
                        "ephemeral_storage_mib"
                    ],
                    "cpu_usage_millicores": (
                        metrics.cpu_usage_millicores if metrics else None
                    ),
                    "memory_usage_mib": (
                        metrics.memory_usage_mib if metrics else None
                    ),
                    "storage_usage_mib": (
                        metrics.storage_usage_mib if metrics else None
                    ),
                }

    return [observations[key] for key in sorted(observations)]


def _decode_summary_response(response: Any) -> dict[str, Any]:
    raw_data = getattr(response, "data", response)
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8")
    if isinstance(raw_data, str):
        parsed = json.loads(raw_data)
    else:
        parsed = raw_data
    if not isinstance(parsed, dict):
        raise ValueError("summary response must be a JSON object")
    return parsed


class KubernetesCollector:
    def __init__(
        self,
        *,
        core_api: client.CoreV1Api,
        node_name: str,
        resource_target_annotation: str,
        resource_target_id_override: str | None,
    ) -> None:
        self._core_api = core_api
        self._node_name = node_name
        self._resource_target_annotation = resource_target_annotation
        self._resource_target_id_override = resource_target_id_override

    @classmethod
    def from_in_cluster(
        cls,
        *,
        node_name: str,
        resource_target_annotation: str,
        resource_target_id_override: str | None,
    ) -> "KubernetesCollector":
        config.load_incluster_config()
        return cls(
            core_api=client.CoreV1Api(),
            node_name=node_name,
            resource_target_annotation=resource_target_annotation,
            resource_target_id_override=resource_target_id_override,
        )

    def _resource_target_id(self, node: Any) -> str:
        annotations = getattr(
            getattr(node, "metadata", None),
            "annotations",
            None,
        ) or {}
        value = self._resource_target_id_override or annotations.get(
            self._resource_target_annotation
        )
        if not value:
            raise ObservationCollectionError(
                "The node does not have a Broker resource target ID annotation"
            )
        try:
            return str(UUID(str(value)))
        except ValueError as error:
            raise ObservationCollectionError(
                "The Broker resource target ID must be a UUID"
            ) from error

    def _read_summary(self) -> dict[str, Any]:
        try:
            response = self._core_api.connect_get_node_proxy_with_path(
                self._node_name,
                "stats/summary",
                _preload_content=False,
            )
            return _decode_summary_response(response)
        except (ApiException, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            LOGGER.warning(
                "Kubelet Summary API is unavailable; usage fields will be null",
                exc_info=True,
            )
            return {}

    def collect(self) -> dict[str, Any]:
        # A commit during collection must not make an earlier Pod list look
        # like a post-commit snapshot, so timestamp before starting the reads.
        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            node = self._core_api.read_node(self._node_name)
            pod_list = self._core_api.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={self._node_name}"
            )
        except ApiException as error:
            raise ObservationCollectionError(
                "Kubernetes node or pod collection failed"
            ) from error

        node_uid = str(
            getattr(getattr(node, "metadata", None), "uid", "") or ""
        )
        if not node_uid:
            raise ObservationCollectionError(
                "Kubernetes node does not contain a stable UID"
            )

        pods = list(getattr(pod_list, "items", None) or [])
        summary = self._read_summary()
        return {
            "resource_target_id": self._resource_target_id(node),
            "observed_at": observed_at,
            "snapshot_complete": True,
            "runtime": {
                "type": "KUBERNETES",
                "target_id": node_uid,
                "ready": _node_ready(node),
            },
            "node_allocatable": _node_allocatable(node).as_capacity(
                round_up=False
            ),
            "allocated_requests": _allocated_requests(pods).as_capacity(
                round_up=True
            ),
            "node_usage": _node_usage(summary),
            "containers": _container_observations(pods, summary),
        }
