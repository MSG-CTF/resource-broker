from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
import shlex
from typing import Any, Protocol

from app.adapters.azure import (
    AzureAdapterError,
    AzureDependencyError,
    AzureResourceNotFoundError,
    GoogleIdTokenProvider,
    _azure_error_code,
    _azure_http_status,
    _optional_non_empty_string,
    _resource_id_component,
    _translate_azure_error,
    azure_federated_credential,
    google_metadata_id_token,
)


class AzureVirtualMachinesOperations(Protocol):
    def list_all(
        self,
        *,
        status_only: str | None = None,
    ) -> Iterable[Any]:
        raise NotImplementedError

    def instance_view(
        self,
        resource_group_name: str,
        vm_name: str,
    ) -> Any:
        raise NotImplementedError


class AzureVirtualMachineRunCommandsOperations(Protocol):
    def begin_create_or_update(
        self,
        resource_group_name: str,
        vm_name: str,
        run_command_name: str,
        run_command: object,
    ) -> Any:
        raise NotImplementedError

    def get_by_virtual_machine(
        self,
        resource_group_name: str,
        vm_name: str,
        run_command_name: str,
        *,
        expand: str | None = None,
    ) -> Any:
        raise NotImplementedError

    def begin_delete(
        self,
        resource_group_name: str,
        vm_name: str,
        run_command_name: str,
    ) -> Any:
        raise NotImplementedError


class AzureBootstrapComputeClient(Protocol):
    virtual_machines: AzureVirtualMachinesOperations
    virtual_machine_run_commands: AzureVirtualMachineRunCommandsOperations


class AzureBootstrapError(AzureAdapterError):
    error_code = "AZURE_BOOTSTRAP_ERROR"
    public_message = "The Azure bootstrap command could not be managed."


class AzureBootstrapTargetNotFoundError(AzureBootstrapError):
    error_code = "AZURE_BOOTSTRAP_TARGET_NOT_FOUND"
    public_message = "The Azure VM was not found in the configured subscription."


class AzureBootstrapTargetNotEnabledError(AzureBootstrapError):
    error_code = "AZURE_BOOTSTRAP_NOT_ENABLED"
    public_message = (
        "The Azure VM does not have the required bootstrap opt-in tag."
    )


class AzureBootstrapTargetNotLinuxError(AzureBootstrapError):
    error_code = "AZURE_BOOTSTRAP_TARGET_NOT_LINUX"
    public_message = "The Azure bootstrap target is not a Linux VM."


class AzureBootstrapVmAgentNotReadyError(AzureBootstrapError):
    error_code = "AZURE_VM_AGENT_NOT_READY"
    public_message = (
        "The Azure VM is not running with a ready Azure Linux Agent."
    )


class AzureBootstrapInvalidResponseError(AzureBootstrapError):
    error_code = "AZURE_RUN_COMMAND_INVALID_RESPONSE"
    public_message = "Azure Run Command returned an invalid response."


@dataclass(frozen=True, slots=True)
class AzureBootstrapTarget:
    tenant_id: str
    client_id: str
    subscription_id: str
    instance_id: str
    instance_name: str
    location: str
    opt_in_tag_key: str
    opt_in_tag_value: str


@dataclass(frozen=True, slots=True)
class AzureBootstrapCommandStatus:
    complete: bool
    succeeded: bool | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedAzureVm:
    resource_group_name: str
    vm_name: str
    location: str
    vm: object


_RUN_COMMAND_NAME = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_PENDING_STATES = frozenset({"unknown", "pending", "running"})
_SUCCESS_STATES = frozenset({"succeeded"})
_FAILED_STATES = frozenset({"failed", "timedout", "canceled", "cancelled"})
def _require_non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty or whitespace")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    return value


def _normalized_enum_value(value: object) -> str | None:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower().replace("_", "")
    return normalized or None


def _runner_download_command(runner_url: str, runner_sha256: str) -> str:
    url = shlex.quote(_require_non_empty_string("runner_url", runner_url))
    digest = shlex.quote(
        _require_non_empty_string("runner_sha256", runner_sha256)
    )
    return f"""set -eu
command -v curl >/dev/null 2>&1 || {{
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends ca-certificates curl
}}
work_dir="$(mktemp -d)"
cleanup() {{ rm -rf -- "${{work_dir}}"; }}
trap cleanup EXIT
curl --fail --silent --show-error --location --retry 3 \
  --proto '=https' --tlsv1.2 {url} --output "${{work_dir}}/runner.sh"
printf '%s  %s\n' {digest} "${{work_dir}}/runner.sh" \
  | sha256sum --check --strict
chmod 0700 "${{work_dir}}/runner.sh"
bash "${{work_dir}}/runner.sh"
"""


def _translate_bootstrap_error(
    subscription_id: str,
    error: Exception,
) -> AzureAdapterError:
    status = _azure_http_status(error)
    code = (_azure_error_code(error) or "").lower()
    if status == 404 or code in {
        "resourcenotfound",
        "resourcegroupnotfound",
    }:
        return AzureBootstrapTargetNotFoundError(subscription_id)
    if code in {
        "operationnotallowed",
        "vmagentstatuscommunicationerror",
        "vmextensionprovisioningerror",
        "vmextensionprovisioningtimeout",
    }:
        return AzureBootstrapVmAgentNotReadyError(subscription_id)
    return _translate_azure_error(subscription_id, error)


class AzureBootstrapAdapter:
    def __init__(
        self,
        target: AzureBootstrapTarget,
        compute_client: AzureBootstrapComputeClient,
    ) -> None:
        self._target = target
        self._compute = compute_client

    @classmethod
    def from_google_oidc(
        cls,
        target: AzureBootstrapTarget,
        id_token_provider: GoogleIdTokenProvider = google_metadata_id_token,
    ) -> "AzureBootstrapAdapter":
        try:
            from azure.mgmt.compute import ComputeManagementClient
        except ModuleNotFoundError as error:
            raise AzureDependencyError(target.subscription_id) from error

        try:
            credential = azure_federated_credential(
                target.tenant_id,
                target.client_id,
                id_token_provider,
            )
            return cls(
                target,
                ComputeManagementClient(
                    credential=credential,
                    subscription_id=_require_non_empty_string(
                        "subscription_id",
                        target.subscription_id,
                    ),
                ),
            )
        except AzureAdapterError:
            raise
        except Exception as error:
            raise _translate_bootstrap_error(
                target.subscription_id,
                error,
            ) from error

    def _resolve_target(self, *, require_opt_in: bool) -> _ResolvedAzureVm:
        expected_instance_id = self._target.instance_id.lower()
        try:
            matches = tuple(
                vm
                for vm in self._compute.virtual_machines.list_all()
                if (
                    _optional_non_empty_string(getattr(vm, "vm_id", None))
                    or ""
                ).lower()
                == expected_instance_id
            )
        except Exception as error:
            raise _translate_bootstrap_error(
                self._target.subscription_id,
                error,
            ) from error
        if len(matches) != 1:
            raise AzureBootstrapTargetNotFoundError(
                self._target.subscription_id
            )

        vm = matches[0]
        vm_id = _optional_non_empty_string(getattr(vm, "id", None))
        vm_name = _optional_non_empty_string(getattr(vm, "name", None))
        location = _optional_non_empty_string(getattr(vm, "location", None))
        if vm_id is None or vm_name is None or location is None:
            raise AzureBootstrapInvalidResponseError(
                self._target.subscription_id
            )
        try:
            resource_group_name = _resource_id_component(
                vm_id,
                "resourceGroups",
            )
        except (TypeError, ValueError) as error:
            raise AzureBootstrapInvalidResponseError(
                self._target.subscription_id
            ) from error

        storage_profile = getattr(vm, "storage_profile", None)
        os_disk = getattr(storage_profile, "os_disk", None)
        os_type = _normalized_enum_value(getattr(os_disk, "os_type", None))
        if os_type != "linux":
            raise AzureBootstrapTargetNotLinuxError(
                self._target.subscription_id
            )

        if require_opt_in:
            tags = getattr(vm, "tags", None)
            normalized_tags = {
                str(key).lower(): str(value).lower()
                for key, value in (
                    tags.items() if isinstance(tags, Mapping) else ()
                )
            }
            if normalized_tags.get(self._target.opt_in_tag_key.lower()) != (
                self._target.opt_in_tag_value.lower()
            ):
                raise AzureBootstrapTargetNotEnabledError(
                    self._target.subscription_id
                )

        return _ResolvedAzureVm(
            resource_group_name=resource_group_name,
            vm_name=vm_name,
            location=location,
            vm=vm,
        )

    def _require_ready_vm_agent(self, vm: _ResolvedAzureVm) -> None:
        try:
            view = self._compute.virtual_machines.instance_view(
                vm.resource_group_name,
                vm.vm_name,
            )
        except Exception as error:
            raise _translate_bootstrap_error(
                self._target.subscription_id,
                error,
            ) from error
        statuses = tuple(getattr(view, "statuses", None) or ())
        status_codes = {
            (_optional_non_empty_string(getattr(status, "code", None)) or "").lower()
            for status in statuses
        }
        vm_agent = getattr(view, "vm_agent", None)
        agent_statuses = tuple(getattr(vm_agent, "statuses", None) or ())
        agent_codes = {
            (_optional_non_empty_string(getattr(status, "code", None)) or "").lower()
            for status in agent_statuses
        }
        if (
            "powerstate/running" not in status_codes
            or "provisioningstate/succeeded" not in agent_codes
        ):
            raise AzureBootstrapVmAgentNotReadyError(
                self._target.subscription_id
            )

    def send_runner(
        self,
        *,
        run_command_name: str,
        runner_url: str,
        runner_sha256: str,
        timeout_seconds: int,
    ) -> str:
        if _RUN_COMMAND_NAME.fullmatch(run_command_name) is None:
            raise AzureBootstrapInvalidResponseError(
                self._target.subscription_id
            )
        vm = self._resolve_target(require_opt_in=True)
        self._require_ready_vm_agent(vm)
        command = _runner_download_command(runner_url, runner_sha256)
        try:
            from azure.mgmt.compute.models import (
                VirtualMachineRunCommand,
                VirtualMachineRunCommandScriptSource,
            )
        except ModuleNotFoundError as error:
            raise AzureDependencyError(self._target.subscription_id) from error

        try:
            poller = (
                self._compute.virtual_machine_run_commands.begin_create_or_update(
                    vm.resource_group_name,
                    vm.vm_name,
                    run_command_name,
                    VirtualMachineRunCommand(
                        location=vm.location,
                        source=VirtualMachineRunCommandScriptSource(
                            script=command,
                        ),
                        async_execution=True,
                        timeout_in_seconds=timeout_seconds,
                        treat_failure_as_deployment_failure=True,
                    ),
                )
            )
            poller.result(timeout=120)
        except Exception as error:
            raise _translate_bootstrap_error(
                self._target.subscription_id,
                error,
            ) from error
        return run_command_name

    def command_status(
        self,
        run_command_name: str,
    ) -> AzureBootstrapCommandStatus:
        vm = self._resolve_target(require_opt_in=False)
        try:
            command = (
                self._compute.virtual_machine_run_commands.get_by_virtual_machine(
                    vm.resource_group_name,
                    vm.vm_name,
                    run_command_name,
                    expand="instanceView",
                )
            )
        except Exception as error:
            raise _translate_bootstrap_error(
                self._target.subscription_id,
                error,
            ) from error

        instance_view = getattr(command, "instance_view", None)
        execution_state = _normalized_enum_value(
            getattr(instance_view, "execution_state", None)
        )
        provisioning_state = _normalized_enum_value(
            getattr(command, "provisioning_state", None)
        )
        if execution_state in _PENDING_STATES or (
            execution_state is None
            and provisioning_state in {"creating", "updating", "succeeded"}
        ):
            return AzureBootstrapCommandStatus(complete=False)
        if execution_state in _SUCCESS_STATES:
            exit_code = getattr(instance_view, "exit_code", None)
            if exit_code == 0:
                return AzureBootstrapCommandStatus(
                    complete=True,
                    succeeded=True,
                )
            return AzureBootstrapCommandStatus(
                complete=True,
                succeeded=False,
                error_code="AZURE_RUN_COMMAND_NONZERO_EXIT",
                error_message=(
                    "Azure Run Command completed with a non-zero exit code."
                ),
            )
        if execution_state in _FAILED_STATES:
            suffix = execution_state.upper()
            return AzureBootstrapCommandStatus(
                complete=True,
                succeeded=False,
                error_code=f"AZURE_RUN_COMMAND_{suffix}",
                error_message=(
                    f"Azure Run Command reported execution state {execution_state}."
                ),
            )
        if execution_state is None and provisioning_state == "failed":
            return AzureBootstrapCommandStatus(
                complete=True,
                succeeded=False,
                error_code="AZURE_RUN_COMMAND_PROVISIONING_FAILED",
                error_message="Azure Run Command provisioning failed.",
            )
        raise AzureBootstrapInvalidResponseError(
            self._target.subscription_id
        )

    def delete_command(self, run_command_name: str) -> None:
        try:
            vm = self._resolve_target(require_opt_in=False)
        except AzureBootstrapTargetNotFoundError:
            return
        try:
            poller = self._compute.virtual_machine_run_commands.begin_delete(
                vm.resource_group_name,
                vm.vm_name,
                run_command_name,
            )
            poller.result(timeout=120)
        except Exception as error:
            if _azure_http_status(error) == 404 or (
                _azure_error_code(error) or ""
            ).lower() in {"resourcenotfound", "resourcenotfounderror"}:
                return
            translated = _translate_bootstrap_error(
                self._target.subscription_id,
                error,
            )
            if isinstance(translated, AzureResourceNotFoundError):
                return
            raise translated from error
