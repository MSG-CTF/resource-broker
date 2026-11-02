from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters.gcp import GcpAdapter
from app.domain.enums import Provider
from app.repositories.provider_accounts import ResourceTargetRepository


POWER_PROJECT_IDS = frozenset({
    "msg-60242078", "msg-60222143", "msg-60222538",
    "msg-60222538-2", "msg-60222143-2", "msg-60251955", "msg-60251955-512519",
    "msg-60222535", "msg-60222535-2", "msg-60222535-3", "msg-60251950",
    "msg-60222513", "msg-602221513-2",
})
KOTH_PROJECT_IDS = frozenset({"msg-60242078", "msg-60222143", "msg-60222538"})


class VmPowerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class VmPowerResult:
    status: str
    vm_status: str
    operation_name: str | None = None


class GcpVmPowerService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._resources = ResourceTargetRepository(session)

    def _target(self, resource_target_id: UUID):
        resource = self._resources.get_for_update(resource_target_id)
        if resource is None:
            raise VmPowerError("RESOURCE_TARGET_NOT_FOUND", "The resource target was not found.")
        account = resource.account
        if (
            account.provider is not Provider.GCP
            or not account.enabled
            or resource.retired_at is not None
            or not resource.provider_scope_id
            or resource.provider_scope_id not in POWER_PROJECT_IDS
            or resource.provider_scope_id not in account.provider_config.get("project_ids", [])
            or not resource.zone
            or not resource.instance_name
        ):
            raise VmPowerError("VM_POWER_NOT_ALLOWED", "This VM is not an active GCP project VM.")
        return resource

    @staticmethod
    def _validate_instance(resource, instance) -> None:
        tags = set(getattr(getattr(instance, "tags", None), "items", ()) or ())
        required_tag = "msg-koth" if resource.provider_scope_id in KOTH_PROJECT_IDS else "msg-problem"
        if (
            str(instance.id) != resource.provider_instance_id
            or instance.name != resource.instance_name
            or required_tag not in tags
        ):
            raise VmPowerError("VM_POWER_NOT_ALLOWED", "The live VM identity or problem tag did not match.")

    def _save_status(self, resource, status: str) -> None:
        resource.provider_instance_state = status
        resource.observed_at = datetime.now(UTC)
        resource.last_seen_at = resource.observed_at
        if status != "RUNNING":
            resource.ready = False
        self._session.commit()

    def start_or_stop(self, resource_target_id: UUID, action: str) -> VmPowerResult:
        resource = self._target(resource_target_id)
        adapter = GcpAdapter.from_adc(resource.provider_scope_id)
        instance = adapter.get_instance(zone=resource.zone, name=resource.instance_name)
        self._validate_instance(resource, instance)
        desired = "RUNNING" if action == "START" else "TERMINATED"
        if instance.status == desired:
            self._save_status(resource, desired)
            return VmPowerResult("DONE", desired)
        if instance.status not in {"RUNNING", "TERMINATED"}:
            raise VmPowerError("VM_POWER_IN_PROGRESS", "The VM is already changing power state.")
        operation_name = adapter.set_power(zone=resource.zone, name=resource.instance_name, action=action)
        self._save_status(resource, "PROVISIONING" if action == "START" else "STOPPING")
        return VmPowerResult("RUNNING", resource.provider_instance_state, operation_name)

    def operation_status(self, resource_target_id: UUID, action: str, operation_name: str) -> VmPowerResult:
        resource = self._target(resource_target_id)
        adapter = GcpAdapter.from_adc(resource.provider_scope_id)
        done, failed, target_id, operation_type = adapter.get_power_operation(
            zone=resource.zone, operation_name=operation_name
        )
        if target_id != resource.provider_instance_id or operation_type != action:
            raise VmPowerError("VM_POWER_OPERATION_MISMATCH", "The operation does not belong to this VM action.")
        if not done:
            return VmPowerResult("RUNNING", resource.provider_instance_state, operation_name)
        if failed:
            raise VmPowerError("VM_POWER_FAILED", "GCP reported that the VM power operation failed.")
        instance = adapter.get_instance(zone=resource.zone, name=resource.instance_name)
        self._validate_instance(resource, instance)
        desired = "RUNNING" if action == "START" else "TERMINATED"
        self._save_status(resource, instance.status)
        if instance.status != desired:
            raise VmPowerError("VM_POWER_STATE_MISMATCH", "The VM did not reach the requested power state.")
        return VmPowerResult("DONE", desired, operation_name)
