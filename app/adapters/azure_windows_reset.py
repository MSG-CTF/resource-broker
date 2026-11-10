from dataclasses import dataclass
from hashlib import sha256
import base64
import ipaddress
import re
from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from cryptography import x509

from app.adapters.azure import azure_federated_credential


class AzureWindowsResetError(RuntimeError):
    def __init__(self, stage: str):
        super().__init__(f"Azure Windows reset failed at {stage}.")
        self.stage = stage


def source_resource_parts(resource_id: str) -> tuple[str, str, str, str]:
    parts = resource_id.strip("/").split("/")
    if (
        len(parts) not in (8, 12)
        or parts[0].lower() != "subscriptions"
        or parts[2].lower() != "resourcegroups"
        or parts[4].lower() != "providers"
        or parts[5].lower() != "microsoft.compute"
        or not parts[3]
    ):
        raise ValueError("A complete Azure snapshot or gallery image version ID is required.")
    if len(parts) == 8 and (parts[6].lower() != "snapshots" or not parts[7]):
        raise ValueError("The Azure snapshot resource ID is invalid.")
    if len(parts) == 12 and (
        parts[6].lower() != "galleries" or parts[8].lower() != "images"
        or parts[10].lower() != "versions" or not all((parts[7], parts[9], parts[11]))
    ):
        raise ValueError("The Azure gallery image version ID is invalid.")
    try:
        subscription_id = str(UUID(parts[1]))
    except ValueError as error:
        raise ValueError("The Azure source subscription ID is invalid.") from error
    return ("snapshot" if len(parts) == 8 else "gallery", subscription_id, parts[3], parts[7])


def _disk_resource_parts(resource_id: str) -> tuple[str, str]:
    parts = resource_id.strip("/").split("/")
    if len(parts) != 8 or parts[2].lower() != "resourcegroups" or parts[5].lower() != "microsoft.compute" or parts[6].lower() != "disks":
        raise AzureWindowsResetError("disk identity")
    return parts[3], parts[7]


def _network_resource_parts(resource_id: str, kind: str, subscription_id: str) -> tuple[str, str]:
    parts = resource_id.strip("/").split("/")
    if (
        len(parts) != 8 or parts[0].lower() != "subscriptions"
        or parts[2].lower() != "resourcegroups" or parts[4].lower() != "providers"
        or parts[5].lower() != "microsoft.network" or parts[6].lower() != kind
        or parts[1].lower() != subscription_id.lower()
    ):
        raise AzureWindowsResetError("network identity")
    return parts[3], parts[7]


def vm_resource_parts(resource_id: str) -> tuple[str, str, str]:
    parts = resource_id.strip("/").split("/")
    if (
        len(parts) != 8 or parts[0].lower() != "subscriptions"
        or parts[2].lower() != "resourcegroups" or parts[4].lower() != "providers"
        or parts[5].lower() != "microsoft.compute" or parts[6].lower() != "virtualmachines"
        or not parts[3] or not parts[7]
    ):
        raise ValueError("A complete Azure VM resource ID is required.")
    try:
        subscription_id = str(UUID(parts[1]))
    except ValueError as error:
        raise ValueError("The Azure VM subscription ID is invalid.") from error
    return subscription_id, parts[3], parts[7]


def _value(value: object) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _disk_policy(disk: object) -> tuple[str, str]:
    security = getattr(getattr(disk, "security_profile", None), "security_type", None)
    encryption = getattr(getattr(disk, "encryption", None), "disk_encryption_set_id", None)
    return _value(security), _value(encryption)


@dataclass(frozen=True)
class AzureWindowsResetTarget:
    tenant_id: str
    client_id: str
    subscription_id: str
    instance_id: str
    source_id: str
    expected_vm_id: str
    execution_id: UUID
    source_tenant_id: str | None = None
    source_client_id: str | None = None


class AzureWindowsResetAdapter:
    def __init__(self, target: AzureWindowsResetTarget, compute, network=None, source_credential=None):
        self.target = target
        self.compute = compute
        self.network = network
        self.source_credential = source_credential

    @classmethod
    def from_google_oidc(cls, target: AzureWindowsResetTarget):
        try:
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.network import NetworkManagementClient

            credential = azure_federated_credential(target.tenant_id, target.client_id)
            source_credential = None
            if source_resource_parts(target.source_id)[1].lower() != target.subscription_id.lower():
                if not target.source_tenant_id or not target.source_client_id:
                    raise AzureWindowsResetError("source federation")
                source_credential = azure_federated_credential(target.source_tenant_id, target.source_client_id)
            return cls(target, ComputeManagementClient(credential, target.subscription_id), NetworkManagementClient(credential, target.subscription_id), source_credential)
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("authentication") from error

    def _source_headers(self) -> dict[str, str]:
        if self.source_credential is None:
            return {}
        try:
            token = self.source_credential.get_token("https://management.azure.com/.default").token
        except Exception as error:
            raise AzureWindowsResetError("source authentication") from error
        return {"x-ms-authorization-auxiliary": f"Bearer {token}"}

    def _vm(self):
        subscription_id, group, name = vm_resource_parts(self.target.expected_vm_id)
        if subscription_id.lower() != self.target.subscription_id.lower():
            raise AzureWindowsResetError("VM subscription")
        try:
            vm = self.compute.virtual_machines.get(group, name)
            if _value(getattr(vm, "vm_id", None)) != self.target.instance_id.lower() or _value(vm.id) != self.target.expected_vm_id.lower():
                raise AzureWindowsResetError("VM identity")
            if _value(getattr(vm.storage_profile.os_disk, "os_type", None)) != "windows":
                raise AzureWindowsResetError("VM operating system")
            return group, name, vm
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("VM lookup") from error

    def _snapshot(self):
        kind, subscription_id, group, name = source_resource_parts(self.target.source_id)
        if kind == "gallery":
            return None
        if subscription_id.lower() != self.target.subscription_id.lower():
            raise AzureWindowsResetError("snapshot subscription")
        try:
            snapshot = self.compute.snapshots.get(group, name)
        except Exception as error:
            raise AzureWindowsResetError("snapshot lookup") from error
        if _value(getattr(snapshot, "id", None)) != self.target.source_id.lower() or _value(getattr(snapshot, "os_type", None)) != "windows":
            raise AzureWindowsResetError("snapshot identity")
        return snapshot

    def _public_ip(self, vm) -> str:
        if self.network is None:
            raise AzureWindowsResetError("network client")
        try:
            for reference in vm.network_profile.network_interfaces or ():
                nic_group, nic_name = _network_resource_parts(reference.id, "networkinterfaces", self.target.subscription_id)
                nic = self.network.network_interfaces.get(nic_group, nic_name)
                for configuration in nic.ip_configurations or ():
                    public_reference = getattr(configuration, "public_ip_address", None)
                    if not public_reference or not public_reference.id:
                        continue
                    ip_group, ip_name = _network_resource_parts(public_reference.id, "publicipaddresses", self.target.subscription_id)
                    address = self.network.public_ip_addresses.get(ip_group, ip_name).ip_address
                    ip = ipaddress.ip_address(address)
                    if ip.is_global:
                        return str(ip)
            raise AzureWindowsResetError("public IP")
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("public IP") from error

    def _healthcheck(self, group: str, name: str, public_ip: str) -> tuple[str, str]:
        from azure.mgmt.compute.models import RunCommandInput

        script = (
            "$ErrorActionPreference='Stop'; "
            "sc.exe config msgdriver start= auto | Out-Null; "
            "if ($LASTEXITCODE -ne 0) { throw 'driver auto-start configuration failed' }; "
            "if ((Get-Service -Name msgdriver).Status -ne 'Running') { "
            "sc.exe start msgdriver | Out-Null; "
            "if ($LASTEXITCODE -ne 0) { throw 'driver start failed' } }; "
            "$result=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\\ctf\\healthcheck.ps1'; "
            "if ($LASTEXITCODE -ne 0) { throw 'healthcheck failed' }; "
            "$check=$result | ConvertFrom-Json; "
            "if ($check.healthy -ne $true -or $check.windows_version -ne '10.0.26200.8875' "
            "-or $check.driver_service -ne 'msgdriver' -or $check.runner_account -ne 'ctfuser') "
            "{ throw 'unexpected challenge state' }; "
            "$exe=Join-Path $env:ProgramFiles 'MSG\\windows-executor\\windows-executor.exe'; "
            "$certPath=Join-Path $env:ProgramFiles 'MSG\\windows-executor\\private\\server.crt'; "
            "$keyPath=Join-Path $env:ProgramFiles 'MSG\\windows-executor\\private\\server.key'; "
            f"& $exe -init-server -server-ip '{public_ip}' -tls-cert $certPath -tls-key $keyPath; "
            "if ($LASTEXITCODE -ne 0) { throw 'executor certificate initialization failed' }; "
            "if ((Get-ScheduledTask -TaskName 'MSGWindowsExecutorAPI').State -ne 'Running') { Start-ScheduledTask -TaskName 'MSGWindowsExecutorAPI' }; "
            "for ($i=0; $i -lt 30 -and -not (Get-NetTCPConnection -LocalPort 443 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }; "
            "if ((Get-ScheduledTask -TaskName 'MSGWindowsExecutorAPI').State -ne 'Running') { throw 'executor API is not running' }; "
            "if (-not (Get-NetTCPConnection -LocalPort 443 -State Listen -ErrorAction SilentlyContinue)) { throw 'executor API is not listening' }; "
            "$pem=Get-Content -LiteralPath $certPath -Raw; "
            "Write-Output ('MSG_EXECUTOR_CERT=' + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pem))); "
            "Write-Output 'MSG_RESET_HEALTH_OK'"
        )
        try:
            result = self.compute.virtual_machines.begin_run_command(group, name, RunCommandInput(command_id="RunPowerShellScript", script=[script])).result()
            output = "\n".join(str(getattr(item, "message", "")) for item in (getattr(result, "value", None) or ()))
            if "MSG_RESET_HEALTH_OK" not in output:
                raise AzureWindowsResetError("guest healthcheck")
            cert_match = re.search(r"MSG_EXECUTOR_CERT=([A-Za-z0-9+/=]+)", output)
            if cert_match is None:
                raise AzureWindowsResetError("executor certificate")
            ip = ipaddress.ip_address(public_ip)
            pem = base64.b64decode(cert_match.group(1), validate=True).decode("ascii")
            cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            if ip not in sans.get_values_for_type(x509.IPAddress):
                raise AzureWindowsResetError("executor certificate IP")
            return pem, str(ip)
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("guest healthcheck") from error

    def restore(self) -> tuple[str, str]:
        from azure.mgmt.compute.models import CreationData, Disk, DiskSku, ImageDiskReference

        group, name, vm = self._vm()
        snapshot = self._snapshot()
        if snapshot is not None and _value(snapshot.location) != _value(vm.location):
            raise AzureWindowsResetError("snapshot location")
        disk_name = f"msgdriver-reset-{self.target.execution_id.hex}"
        os_disk = vm.storage_profile.os_disk
        current_id = getattr(getattr(os_disk, "managed_disk", None), "id", None)
        if not current_id:
            raise AzureWindowsResetError("OS disk identity")
        try:
            old_group, old_name = _disk_resource_parts(current_id)
            current = self.compute.disks.get(old_group, old_name)
            if _value(getattr(current, "os_type", None)) != "windows" or (
                snapshot is not None and (
                    current.disk_size_gb != snapshot.disk_size_gb
                    or _value(getattr(current, "hyper_v_generation", None)) != _value(getattr(snapshot, "hyper_v_generation", None))
                    or _disk_policy(current) != _disk_policy(snapshot)
                )
            ):
                raise AzureWindowsResetError("disk compatibility")
            try:
                replacement = self.compute.disks.get(group, disk_name)
            except ResourceNotFoundError:
                replacement = None
            if replacement is None:
                creation_data = (
                    CreationData(create_option="Copy", source_resource_id=snapshot.id)
                    if snapshot is not None else
                    CreationData(create_option="FromImage", gallery_image_reference=ImageDiskReference(id=self.target.source_id))
                )
                replacement = self.compute.disks.begin_create_or_update(
                    group,
                    disk_name,
                    Disk(
                        location=vm.location,
                        sku=DiskSku(name=current.sku.name),
                        security_profile=current.security_profile if snapshot is None else None,
                        disk_size_gb=current.disk_size_gb,
                        zones=getattr(vm, "zones", None) or None,
                        creation_data=creation_data,
                        tags={
                            "msgdriver-execution-id": str(self.target.execution_id),
                            "msgdriver-vm-id": self.target.instance_id,
                            "msgdriver-source-sha256": sha256(self.target.source_id.lower().encode()).hexdigest(),
                            "msgdriver-previous-disk-rg": old_group,
                            "msgdriver-previous-disk-name": old_name,
                        },
                    ),
                    headers=self._source_headers(),
                ).result()
            if (
                (replacement.tags or {}).get("msgdriver-execution-id") != str(self.target.execution_id)
                or (replacement.tags or {}).get("msgdriver-vm-id") != self.target.instance_id
                or (replacement.tags or {}).get("msgdriver-source-sha256") != sha256(self.target.source_id.lower().encode()).hexdigest()
                or _value(getattr(replacement, "os_type", None)) != "windows"
                or replacement.disk_size_gb != current.disk_size_gb
                or _value(getattr(replacement, "hyper_v_generation", None)) != _value(getattr(current, "hyper_v_generation", None))
                or _disk_policy(replacement) != _disk_policy(current)
                or _value(getattr(getattr(replacement, "sku", None), "name", None)) != _value(current.sku.name)
            ):
                raise AzureWindowsResetError("replacement disk identity")
            if _value(current_id) != _value(replacement.id):
                if (
                    _value((replacement.tags or {}).get("msgdriver-previous-disk-rg")) != old_group.lower()
                    or _value((replacement.tags or {}).get("msgdriver-previous-disk-name")) != old_name.lower()
                ):
                    raise AzureWindowsResetError("previous disk identity")
                if getattr(replacement, "managed_by", None):
                    raise AzureWindowsResetError("replacement disk already attached")
                self.compute.virtual_machines.begin_deallocate(group, name).result()
                vm = self.compute.virtual_machines.get(group, name)
                if (
                    _value(vm.vm_id) != self.target.instance_id.lower()
                    or _value(vm.storage_profile.os_disk.managed_disk.id) != _value(current_id)
                ):
                    raise AzureWindowsResetError("VM identity")
                # Azure requires the original createOption and image reference on VM update.
                vm.storage_profile.os_disk.managed_disk.id = replacement.id
                vm.storage_profile.os_disk.name = replacement.name
                self.compute.virtual_machines.begin_create_or_update(group, name, vm, headers=self._source_headers()).result()
            view = self.compute.virtual_machines.instance_view(group, name)
            running = any(_value(getattr(status, "code", None)) == "powerstate/running" for status in (getattr(view, "statuses", None) or ()))
            if not running:
                self.compute.virtual_machines.begin_start(group, name).result()
            vm = self.compute.virtual_machines.get(group, name)
            if _value(vm.vm_id) != self.target.instance_id.lower() or _value(vm.storage_profile.os_disk.managed_disk.id) != _value(replacement.id):
                raise AzureWindowsResetError("OS disk verification")
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("OS disk replacement") from error
        result = self._healthcheck(group, name, self._public_ip(vm))
        previous_group = (replacement.tags or {}).get("msgdriver-previous-disk-rg")
        previous_name = (replacement.tags or {}).get("msgdriver-previous-disk-name")
        # shortcut: disks created before these tags cannot be identified after a retry; inspect them manually.
        if previous_group is None and previous_name is None:
            return result
        if not previous_group or not previous_name:
            raise AzureWindowsResetError("previous disk identity")
        try:
            vm = self._vm()[2]
            if _value(vm.storage_profile.os_disk.managed_disk.id) != _value(replacement.id):
                raise AzureWindowsResetError("OS disk verification")
            try:
                previous = self.compute.disks.get(previous_group, previous_name)
            except ResourceNotFoundError:
                return result
            expected_id = (
                f"/subscriptions/{self.target.subscription_id}/resourceGroups/{previous_group}"
                f"/providers/Microsoft.Compute/disks/{previous_name}"
            )
            if (
                _value(previous.id) != expected_id.lower()
                or _value(previous.id) == _value(replacement.id)
                or getattr(previous, "managed_by", None)
                or getattr(previous, "managed_by_extended", None)
            ):
                raise AzureWindowsResetError("previous disk identity")
            self.compute.disks.begin_delete(previous_group, previous_name).result()
        except AzureWindowsResetError:
            raise
        except Exception as error:
            raise AzureWindowsResetError("previous disk cleanup") from error
        return result
