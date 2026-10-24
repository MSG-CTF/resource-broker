from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.adapters.gcp import GcpAdapterError, _translate_google_error


class GcpBootstrapConflictError(GcpAdapterError):
    error_code = "GCP_BOOTSTRAP_LABEL_CONFLICT"
    public_message = "The VM already has the reserved bootstrap label."


class GcpBootstrapCleanupError(GcpAdapterError):
    error_code = "GCP_BOOTSTRAP_CLEANUP_FAILED"
    public_message = "The temporary GCP bootstrap resources could not be cleaned up."


class InstancesClient(Protocol):
    def get(self, **kwargs: Any) -> Any: ...
    def set_labels(self, **kwargs: Any) -> Any: ...


class OsConfigClient(Protocol):
    def create_os_policy_assignment(self, **kwargs: Any) -> Any: ...
    def get_os_policy_assignment(self, **kwargs: Any) -> Any: ...
    def get_os_policy_assignment_report(self, **kwargs: Any) -> Any: ...
    def delete_os_policy_assignment(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class GcpBootstrapTarget:
    project_id: str
    zone: str
    instance_name: str
    instance_id: str


class GcpBootstrapAdapter:
    def __init__(
        self,
        target: GcpBootstrapTarget,
        instances_client: InstancesClient,
        os_config_client: OsConfigClient,
        compute_types: Any,
        osconfig_types: Any,
    ) -> None:
        self._target = target
        self._instances = instances_client
        self._os_config = os_config_client
        self._compute_types = compute_types
        self._osconfig_types = osconfig_types

    @classmethod
    def from_adc(cls, target: GcpBootstrapTarget) -> GcpBootstrapAdapter:
        try:
            from google.cloud import compute_v1, osconfig_v1
        except ModuleNotFoundError as error:
            raise GcpAdapterError(target.project_id) from error
        return cls(
            target,
            compute_v1.InstancesClient(),
            osconfig_v1.OsConfigZonalServiceClient(),
            compute_v1,
            osconfig_v1,
        )

    def _instance(self) -> Any:
        return self._instances.get(
            project=self._target.project_id,
            zone=self._target.zone,
            instance=self._target.instance_name,
        )

    def _set_labels(self, labels: dict[str, str], fingerprint: str) -> None:
        body = self._compute_types.InstancesSetLabelsRequest(
            labels=labels,
            label_fingerprint=fingerprint,
        )
        operation = self._instances.set_labels(
            project=self._target.project_id,
            zone=self._target.zone,
            instance=self._target.instance_name,
            instances_set_labels_request_resource=body,
        )
        operation.result(timeout=60)

    def ensure_temporary_label(self, key: str, value: str) -> None:
        try:
            instance = self._instance()
            labels = dict(getattr(instance, "labels", None) or {})
            existing = labels.get(key)
            if existing == value:
                return
            if existing is not None:
                raise GcpBootstrapConflictError(self._target.project_id)
            labels[key] = value
            self._set_labels(labels, instance.label_fingerprint)
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_google_error(self._target.project_id, error) from error

    def remove_temporary_label(self, key: str, value: str) -> None:
        try:
            instance = self._instance()
            labels = dict(getattr(instance, "labels", None) or {})
            existing = labels.get(key)
            if existing is None:
                return
            if existing != value:
                raise GcpBootstrapCleanupError(self._target.project_id)
            del labels[key]
            self._set_labels(labels, instance.label_fingerprint)
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_google_error(self._target.project_id, error) from error

    def ensure_assignment(
        self,
        *,
        assignment_id: str,
        label_key: str,
        label_value: str,
        runner_url: str,
        runner_sha256: str,
        job_id: str,
    ) -> None:
        assignment = self._osconfig_types.OSPolicyAssignment(
            description=f"MSG Broker bootstrap job {job_id}",
            instance_filter={
                "inclusion_labels": [
                    {"labels": {label_key: label_value}},
                ],
            },
            os_policies=[
                {
                    "id": "msg-broker-bootstrap",
                    "mode": "ENFORCEMENT",
                    "resource_groups": [
                        {
                            "resources": [
                                {
                                    "id": "runner",
                                    "exec": {
                                        "validate": {
                                            "script": (
                                                "#!/bin/sh\n"
                                                f"test -f /var/lib/msg-broker-bootstrap/jobs/{job_id}.done "
                                                "&& exit 100 || exit 101\n"
                                            ),
                                            "interpreter": "SHELL",
                                        },
                                        "enforce": {
                                            "file": {
                                                "remote": {
                                                    "uri": runner_url,
                                                    "sha256_checksum": runner_sha256,
                                                }
                                            },
                                            "interpreter": "SHELL",
                                        },
                                    },
                                }
                            ]
                        }
                    ],
                }
            ],
            rollout={
                "disruption_budget": {"fixed": 1},
                "min_wait_duration": {"seconds": 0},
            },
        )
        parent = (
            f"projects/{self._target.project_id}/locations/{self._target.zone}"
        )
        try:
            operation = self._os_config.create_os_policy_assignment(
                parent=parent,
                os_policy_assignment=assignment,
                os_policy_assignment_id=assignment_id,
            )
            operation.result(timeout=120)
        except Exception as error:
            try:
                from google.api_core.exceptions import AlreadyExists
            except ModuleNotFoundError:
                AlreadyExists = ()
            if isinstance(error, AlreadyExists):
                return
            raise _translate_google_error(self._target.project_id, error) from error

    def assignment_exists(self, assignment_id: str) -> bool:
        name = (
            f"projects/{self._target.project_id}/locations/{self._target.zone}"
            f"/osPolicyAssignments/{assignment_id}"
        )
        try:
            self._os_config.get_os_policy_assignment(
                name=name,
                retry=None,
                timeout=30,
            )
        except Exception as error:
            try:
                from google.api_core.exceptions import NotFound
            except ModuleNotFoundError:
                NotFound = ()
            if isinstance(error, NotFound):
                return False
            raise _translate_google_error(self._target.project_id, error) from error
        return True

    def compliance(self, assignment_id: str) -> bool | None:
        name = (
            f"projects/{self._target.project_id}/locations/{self._target.zone}"
            f"/instances/{self._target.instance_id}"
            f"/osPolicyAssignments/{assignment_id}/report"
        )
        try:
            report = self._os_config.get_os_policy_assignment_report(name=name)
        except Exception as error:
            try:
                from google.api_core.exceptions import NotFound
            except ModuleNotFoundError:
                NotFound = ()
            if isinstance(error, NotFound):
                return None
            raise _translate_google_error(self._target.project_id, error) from error
        compliances = tuple(
            getattr(report, "os_policy_compliances", None) or ()
        )
        if not compliances:
            return None

        state_names = tuple(
            self._compliance_state_name(
                getattr(compliance, "compliance_state", None)
            )
            for compliance in compliances
        )
        if all(
            name.endswith("COMPLIANT") and "NON_COMPLIANT" not in name
            for name in state_names
        ):
            return True
        if any(
            "NON_COMPLIANT" not in name
            and not name.endswith("COMPLIANT")
            for name in state_names
        ):
            return None
        non_compliant = tuple(
            compliance
            for compliance, state_name in zip(
                compliances,
                state_names,
                strict=True,
            )
            if "NON_COMPLIANT" in state_name
        )
        if non_compliant and all(
            self._non_compliance_is_final(compliance)
            for compliance in non_compliant
        ):
            return False
        return None

    def _non_compliance_is_final(self, compliance: object) -> bool:
        resources = tuple(
            getattr(compliance, "os_policy_resource_compliances", None) or ()
        )
        if not resources:
            return False

        state_names = tuple(
            self._compliance_state_name(
                getattr(resource, "compliance_state", None)
            )
            for resource in resources
        )
        if any(
            "NON_COMPLIANT" not in name
            and not name.endswith("COMPLIANT")
            for name in state_names
        ):
            return False
        failed_resources = tuple(
            resource
            for resource, state_name in zip(
                resources,
                state_names,
                strict=True,
            )
            if "NON_COMPLIANT" in state_name
        )
        if not failed_resources:
            return False
        return all(
            any(
                self._is_post_enforcement_step(step)
                for step in tuple(getattr(resource, "config_steps", None) or ())
            )
            for resource in failed_resources
        )

    @staticmethod
    def _is_post_enforcement_step(step: object) -> bool:
        # OSPolicyResourceConfigStep.Type value 4 is the final desired-state
        # check performed after enforcement. Proto-plus exposes this field as
        # ``type_`` because ``type`` is a Python built-in.
        step_type = getattr(step, "type_", getattr(step, "type", None))
        name = getattr(step_type, "name", None)
        if isinstance(name, str):
            return name.upper() == "DESIRED_STATE_CHECK_POST_ENFORCEMENT"
        try:
            return int(step_type) == 4
        except (TypeError, ValueError):
            return False

    def _compliance_state_name(self, state: object) -> str:
        name = getattr(state, "name", None)
        if isinstance(name, str):
            return name.upper()

        try:
            compliance_state = (
                self._osconfig_types.OSPolicyAssignmentReport
                .OSPolicyCompliance.ComplianceState
            )
            return compliance_state(state).name.upper()
        except (AttributeError, TypeError, ValueError):
            return str(state).upper()

    def delete_assignment(self, assignment_id: str) -> None:
        name = (
            f"projects/{self._target.project_id}/locations/{self._target.zone}"
            f"/osPolicyAssignments/{assignment_id}"
        )
        try:
            operation = self._os_config.delete_os_policy_assignment(name=name)
            operation.result(timeout=120)
        except TypeError as error:
            # Some successful delete LROs fail while decoding the Empty
            # response. Confirm the resource is actually gone before treating
            # that client-side error as a cleanup failure.
            if not self.assignment_exists(assignment_id):
                return
            raise _translate_google_error(self._target.project_id, error) from error
        except Exception as error:
            try:
                from google.api_core.exceptions import NotFound
            except ModuleNotFoundError:
                NotFound = ()
            if isinstance(error, NotFound):
                return
            raise _translate_google_error(self._target.project_id, error) from error
