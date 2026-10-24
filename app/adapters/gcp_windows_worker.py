from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol
from uuid import UUID

from app.adapters.gcp import (
    GcpAdapterError,
    GcpDependencyError,
    _instance_ip_addresses,
    _translate_google_error,
)


class GcpWindowsWorkerConfigurationError(GcpAdapterError):
    error_code = "WINDOWS_WORKER_CONTROLLER_NOT_CONFIGURED"
    public_message = "The Windows worker controller identity is not configured."


class GcpWindowsWorkerPermissionError(GcpAdapterError):
    error_code = "GCP_PERMISSION_DENIED"
    public_message = (
        "The Windows worker controller cannot access this project."
    )


def _translate_controller_error(
    project_id: str,
    error: Exception,
) -> GcpAdapterError:
    translated = _translate_google_error(project_id, error)
    if translated.error_code == "GCP_PERMISSION_DENIED":
        return GcpWindowsWorkerPermissionError(project_id)
    return translated


class InstanceGroupManagersClient(Protocol):
    def list_managed_instances(self, *, request: object) -> Any: ...
    def recreate_instances(self, *, request: object) -> Any: ...


class InstancesClient(Protocol):
    def get(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class GcpWindowsWorkerPool:
    project_id: str
    zone: str
    managed_instance_group: str


@dataclass(frozen=True, slots=True)
class ManagedWindowsInstance:
    instance_name: str
    instance_id: str
    running: bool
    idle: bool
    healthy: bool


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    return str(value).rsplit(".", maxsplit=1)[-1].upper()


class GcpWindowsWorkerAdapter:
    def __init__(
        self,
        pool: GcpWindowsWorkerPool,
        group_managers: InstanceGroupManagersClient,
        instances: InstancesClient,
        compute_types: Any,
    ) -> None:
        self._pool = pool
        self._group_managers = group_managers
        self._instances = instances
        self._compute_types = compute_types

    @classmethod
    def from_adc(
        cls,
        pool: GcpWindowsWorkerPool,
    ) -> GcpWindowsWorkerAdapter:
        controller = os.getenv(
            "WINDOWS_WORKER_CONTROLLER_SERVICE_ACCOUNT",
            "",
        ).strip()
        if not controller.endswith(".iam.gserviceaccount.com"):
            raise GcpWindowsWorkerConfigurationError(pool.project_id)

        try:
            import google.auth
            from google.auth import impersonated_credentials
            from google.cloud import compute_v1
        except ModuleNotFoundError as error:
            raise GcpDependencyError(pool.project_id) from error

        try:
            source_credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=controller,
                target_scopes=[
                    "https://www.googleapis.com/auth/cloud-platform"
                ],
                lifetime=600,
            )
            return cls(
                pool,
                compute_v1.InstanceGroupManagersClient(
                    credentials=credentials
                ),
                compute_v1.InstancesClient(credentials=credentials),
                compute_v1,
            )
        except Exception as error:
            raise _translate_controller_error(pool.project_id, error) from error

    def list_managed_instances(self) -> tuple[ManagedWindowsInstance, ...]:
        request = (
            self._compute_types.ListManagedInstancesInstanceGroupManagersRequest(
                project=self._pool.project_id,
                zone=self._pool.zone,
                instance_group_manager=self._pool.managed_instance_group,
            )
        )
        try:
            instances = self._group_managers.list_managed_instances(
                request=request
            )
            return tuple(self._normalize(item) for item in instances)
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_controller_error(
                self._pool.project_id,
                error,
            ) from error

    def recreate(self, instance_name: str, request_id: UUID) -> None:
        body = self._compute_types.InstanceGroupManagersRecreateInstancesRequest(
            instances=[
                f"zones/{self._pool.zone}/instances/{instance_name}"
            ]
        )
        request = self._compute_types.RecreateInstancesInstanceGroupManagerRequest(
            project=self._pool.project_id,
            zone=self._pool.zone,
            instance_group_manager=self._pool.managed_instance_group,
            instance_group_managers_recreate_instances_request_resource=body,
            request_id=str(request_id),
        )
        try:
            operation = self._group_managers.recreate_instances(
                request=request
            )
            operation.result(timeout=60)
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_controller_error(
                self._pool.project_id,
                error,
            ) from error

    def private_ip(self, instance_name: str) -> str | None:
        try:
            instance = self._instances.get(
                project=self._pool.project_id,
                zone=self._pool.zone,
                instance=instance_name,
            )
            private_ip, _ = _instance_ip_addresses(instance)
            return private_ip
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_controller_error(
                self._pool.project_id,
                error,
            ) from error

    @staticmethod
    def _normalize(instance: object) -> ManagedWindowsInstance:
        instance_url = str(getattr(instance, "instance", ""))
        instance_name = instance_url.rsplit("/", maxsplit=1)[-1]
        instance_id = str(getattr(instance, "id", ""))
        if not instance_name or not instance_id:
            raise ValueError("Managed instance identity is missing")

        health = tuple(getattr(instance, "instance_health", None) or ())
        return ManagedWindowsInstance(
            instance_name=instance_name,
            instance_id=instance_id,
            running=_enum_name(
                getattr(instance, "instance_status", None)
            )
            == "RUNNING",
            idle=_enum_name(getattr(instance, "current_action", None))
            == "NONE",
            healthy=bool(health)
            and all(
                _enum_name(
                    getattr(item, "detailed_health_state", None)
                )
                == "HEALTHY"
                for item in health
            ),
        )
