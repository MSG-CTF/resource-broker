from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from app.adapters.base import ProviderInstanceSummary
from app.domain.enums import Architecture


class AzureVirtualMachinesOperations(Protocol):
    def list(self, resource_group_name: str) -> Iterable[Any]:
        raise NotImplementedError

    def get(self, resource_group_name: str, vm_name: str, *, expand: str) -> Any:
        raise NotImplementedError

    def list_all(
        self,
        *,
        status_only: str | None = None,
    ) -> Iterable[Any]:
        raise NotImplementedError


class AzureNetworkInterfacesOperations(Protocol):
    def get(
        self,
        resource_group_name: str,
        network_interface_name: str,
    ) -> Any:
        raise NotImplementedError


class AzurePublicIpAddressesOperations(Protocol):
    def get(
        self,
        resource_group_name: str,
        public_ip_address_name: str,
    ) -> Any:
        raise NotImplementedError


class AzureResourceSkusOperations(Protocol):
    def list(
        self,
        *,
        filter: str | None = None,
    ) -> Iterable[Any]:
        raise NotImplementedError


class AzureComputeClient(Protocol):
    virtual_machines: AzureVirtualMachinesOperations
    resource_skus: AzureResourceSkusOperations


class AzureNetworkClient(Protocol):
    network_interfaces: AzureNetworkInterfacesOperations
    public_ip_addresses: AzurePublicIpAddressesOperations


AzureCredentialFactory = Callable[..., object]
AzureComputeClientFactory = Callable[..., AzureComputeClient]
AzureNetworkClientFactory = Callable[..., AzureNetworkClient]
GoogleIdTokenProvider = Callable[[str], str]


AZURE_TOKEN_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"


class AzureAdapterError(RuntimeError):
    error_code = "AZURE_API_ERROR"
    public_message = "Azure API request failed."

    def __init__(self, subscription_id: str) -> None:
        self.subscription_id = subscription_id
        super().__init__(self.public_message)


class AzureDependencyError(AzureAdapterError):
    error_code = "AZURE_SDK_NOT_INSTALLED"
    public_message = "The Azure SDK dependency is not installed."


class AzureAuthenticationError(AzureAdapterError):
    error_code = "AZURE_AUTHENTICATION_FAILED"
    public_message = "Azure authentication failed."


class AzureUserLoginRequiredError(AzureAuthenticationError):
    error_code = "AZURE_USER_LOGIN_REQUIRED"
    public_message = "Microsoft 계정 연결을 완료한 뒤 다시 조회하세요."


class AzurePermissionError(AzureAdapterError):
    error_code = "AZURE_PERMISSION_DENIED"
    public_message = (
        "The broker service principal cannot access this subscription."
    )


class AzureSubscriptionNotFoundError(AzurePermissionError):
    error_code = "AZURE_SUBSCRIPTION_NOT_FOUND"
    public_message = "The Azure subscription was not found or is not accessible."


class AzureResourceNotFoundError(AzureAdapterError):
    error_code = "AZURE_RESOURCE_NOT_FOUND"
    public_message = (
        "An Azure VM network resource was not found while collecting VM details."
    )


class AzureApiUnavailableError(AzureAdapterError):
    error_code = "AZURE_API_UNAVAILABLE"
    public_message = "The Azure API is unavailable."


def _require_non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty or whitespace")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    return value


def google_metadata_id_token(audience: str) -> str:
    try:
        from google.auth.compute_engine.credentials import IDTokenCredentials
        from google.auth.transport.requests import Request
    except ModuleNotFoundError as error:
        raise AzureDependencyError("federation") from error

    try:
        request = Request()
        credentials = IDTokenCredentials(
            request,
            target_audience=_require_non_empty_string(
                "audience",
                audience,
            ),
            use_metadata_identity_endpoint=True,
        )
        credentials.refresh(request)
        return _require_non_empty_string(
            "google_id_token",
            credentials.token,
        )
    except AzureAdapterError:
        raise
    except Exception as error:
        raise AzureAuthenticationError("federation") from error


def azure_federated_credential(
    tenant_id: str,
    client_id: str,
    id_token_provider: GoogleIdTokenProvider = google_metadata_id_token,
) -> object:
    try:
        from azure.identity import ClientAssertionCredential
    except ModuleNotFoundError as error:
        raise AzureDependencyError("federation") from error

    return ClientAssertionCredential(
        tenant_id=_require_non_empty_string("tenant_id", tenant_id),
        client_id=_require_non_empty_string("client_id", client_id),
        func=lambda: id_token_provider(AZURE_TOKEN_EXCHANGE_AUDIENCE),
    )


def _optional_non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _resource_id_component(resource_id: object, component: str) -> str:
    value = _require_non_empty_string("resource_id", resource_id)
    segments = tuple(segment for segment in value.split("/") if segment)
    component_lower = component.lower()

    for index, segment in enumerate(segments[:-1]):
        if segment.lower() == component_lower:
            return _require_non_empty_string(component, segments[index + 1])

    raise ValueError(f"resource_id has no {component} component")


def _primary_first(items: object) -> tuple[Any, ...]:
    values = tuple(items or ())
    return tuple(
        sorted(
            values,
            key=lambda item: not bool(getattr(item, "primary", False)),
        )
    )


def _power_state(vm: object) -> str:
    instance_view = getattr(vm, "instance_view", None)
    statuses = getattr(instance_view, "statuses", None) or ()

    for status in reversed(tuple(statuses)):
        code = _optional_non_empty_string(getattr(status, "code", None))
        if code is not None and code.lower().startswith("powerstate/"):
            return code.rsplit("/", maxsplit=1)[-1].upper()

    provisioning_state = _optional_non_empty_string(
        getattr(vm, "provisioning_state", None)
    )
    return provisioning_state.upper() if provisioning_state else "UNKNOWN"


def _availability_zone(vm: object) -> str | None:
    for zone in getattr(vm, "zones", None) or ():
        normalized = _optional_non_empty_string(zone)
        if normalized is not None:
            return normalized
    return None


def _machine_type(vm: object) -> str:
    hardware_profile = getattr(vm, "hardware_profile", None)
    return (
        _optional_non_empty_string(getattr(hardware_profile, "vm_size", None))
        or "UNKNOWN"
    )


def _vm_lookup_key(vm: object) -> str | None:
    for attribute_name in ("vm_id", "id"):
        value = _optional_non_empty_string(
            getattr(vm, attribute_name, None)
        )
        if value is not None:
            return value.lower()

    location = _optional_non_empty_string(getattr(vm, "location", None))
    name = _optional_non_empty_string(getattr(vm, "name", None))
    if location is None or name is None:
        return None
    return f"{location.lower()}::{name.lower()}"


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _attached_storage_mib(vm: object) -> int | None:
    storage_profile = getattr(vm, "storage_profile", None)
    os_disk = getattr(storage_profile, "os_disk", None)
    data_disks = getattr(storage_profile, "data_disks", None) or ()
    disk_sizes_gib = [
        disk_size_gib
        for disk in (os_disk, *tuple(data_disks))
        if disk is not None
        and (
            disk_size_gib := _optional_non_negative_int(
                getattr(disk, "disk_size_gb", None)
            )
        )
        is not None
    ]
    if not disk_sizes_gib:
        return None
    return sum(disk_sizes_gib) * 1024


def _sku_capabilities(sku: object) -> dict[str, str]:
    capabilities: dict[str, str] = {}
    for capability in getattr(sku, "capabilities", None) or ():
        name = _optional_non_empty_string(getattr(capability, "name", None))
        value = _optional_non_empty_string(getattr(capability, "value", None))
        if name is not None and value is not None:
            capabilities[name.lower()] = value
    return capabilities


def _capability_int(
    capabilities: dict[str, str],
    *names: str,
) -> int | None:
    for name in names:
        value = capabilities.get(name.lower())
        if value is None:
            continue
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            continue
        if parsed < 0 or parsed != parsed.to_integral_value():
            continue
        return int(parsed)
    return None


def _memory_mib(capabilities: dict[str, str]) -> int | None:
    value = capabilities.get("memorygb")
    if value is None:
        return None
    try:
        memory_mib = Decimal(value) * 1024
    except InvalidOperation:
        return None
    if memory_mib < 0 or memory_mib != memory_mib.to_integral_value():
        return None
    return int(memory_mib)


def _architecture(capabilities: dict[str, str]) -> Architecture | None:
    value = capabilities.get("cpuarchitecturetype")
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized in {"X64", "X86_64", "AMD64"}:
        return Architecture.AMD64
    if normalized in {"ARM64", "AARCH64"}:
        return Architecture.ARM64
    return None


def _azure_error_code(error: Exception) -> str | None:
    details = getattr(error, "error", None)
    return _optional_non_empty_string(getattr(details, "code", None))


def _azure_http_status(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _translate_azure_error(
    subscription_id: str,
    error: Exception,
) -> AzureAdapterError:
    try:
        from azure.core import exceptions as azure_exceptions
    except ModuleNotFoundError:
        return AzureDependencyError(subscription_id)

    if isinstance(error, azure_exceptions.ClientAuthenticationError):
        return AzureAuthenticationError(subscription_id)

    if isinstance(error, azure_exceptions.ResourceNotFoundError):
        if (_azure_error_code(error) or "").lower() == "subscriptionnotfound":
            return AzureSubscriptionNotFoundError(subscription_id)
        return AzureResourceNotFoundError(subscription_id)

    if isinstance(
        error,
        (
            azure_exceptions.ServiceRequestError,
            azure_exceptions.ServiceResponseError,
        ),
    ):
        return AzureApiUnavailableError(subscription_id)

    if isinstance(error, (azure_exceptions.HttpResponseError, requests.HTTPError)):
        status_code = _azure_http_status(error)
        if status_code == 401:
            return AzureAuthenticationError(subscription_id)
        if status_code == 403:
            return AzurePermissionError(subscription_id)
        if status_code == 404:
            if (_azure_error_code(error) or "").lower() == "subscriptionnotfound":
                return AzureSubscriptionNotFoundError(subscription_id)
            return AzureResourceNotFoundError(subscription_id)
        if status_code in {408, 429} or (
            status_code is not None and status_code >= 500
        ):
            return AzureApiUnavailableError(subscription_id)

    if isinstance(error, azure_exceptions.AzureError):
        return AzureAdapterError(subscription_id)
    if isinstance(error, requests.RequestException):
        return AzureApiUnavailableError(subscription_id)
    return AzureAdapterError(subscription_id)


class AzureAdapter:
    def __init__(
        self,
        subscription_id: str,
        compute_client: AzureComputeClient,
        network_client: AzureNetworkClient,
        user_credential: Any = None,
    ) -> None:
        self._subscription_id = _require_non_empty_string(
            "subscription_id",
            subscription_id,
        )
        self._compute_client = compute_client
        self._network_client = network_client
        self._user_credential = user_credential
        self._sku_cache: dict[
            tuple[str, str],
            tuple[int | None, int | None, Architecture | None],
        ] = {}
        self._loaded_sku_locations: set[str] = set()

    @classmethod
    def from_google_oidc(
        cls,
        tenant_id: str,
        client_id: str,
        subscription_id: str,
        id_token_provider: GoogleIdTokenProvider = google_metadata_id_token,
    ) -> "AzureAdapter":
        normalized_subscription_id = _require_non_empty_string(
            "subscription_id",
            subscription_id,
        )
        try:
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.network import NetworkManagementClient
        except ModuleNotFoundError as error:
            raise AzureDependencyError(normalized_subscription_id) from error

        try:
            credential = azure_federated_credential(
                tenant_id,
                client_id,
                id_token_provider,
            )
            return cls(
                subscription_id=normalized_subscription_id,
                compute_client=ComputeManagementClient(
                    credential=credential,
                    subscription_id=normalized_subscription_id,
                ),
                network_client=NetworkManagementClient(
                    credential=credential,
                    subscription_id=normalized_subscription_id,
                ),
            )
        except AzureAdapterError:
            raise
        except Exception as error:
            raise _translate_azure_error(
                normalized_subscription_id,
                error,
            ) from error

    @classmethod
    def from_user_credential(cls, subscription_id: str, credential: Any) -> "AzureAdapter":
        try:
            from azure.mgmt.compute import ComputeManagementClient
            from azure.mgmt.network import NetworkManagementClient
        except ModuleNotFoundError as error:
            raise AzureDependencyError(subscription_id) from error
        return cls(
            subscription_id,
            ComputeManagementClient(credential, subscription_id),
            NetworkManagementClient(credential, subscription_id),
            user_credential=credential,
        )

    def _user_resource_groups(self) -> Iterable[str]:
        # Resource-group roles do not grant subscription-wide VM/SKU reads.
        prefix = f"https://management.azure.com/subscriptions/{self._subscription_id}/resourcegroups"
        url = f"{prefix}?api-version=2021-04-01"
        visited: set[str] = set()
        while url:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "management.azure.com"
                    or parsed.path.lower() != urlsplit(prefix).path.lower() or url in visited):
                raise AzureApiUnavailableError(self._subscription_id)
            visited.add(url)
            token = self._user_credential.get_token("https://management.azure.com/.default")
            response = requests.get(
                url, headers={"Authorization": f"Bearer {token.token}"},
                timeout=30, allow_redirects=False,
            )
            response.raise_for_status()
            if response.status_code != 200:
                raise AzureApiUnavailableError(self._subscription_id)
            payload = response.json()
            for group in payload["value"]:
                yield _require_non_empty_string("resource_group.name", group.get("name"))
            url = payload.get("nextLink")

    def check_user_access(self) -> None:
        try:
            next(iter(self._user_resource_groups()), None)
        except AzureAdapterError:
            raise
        except Exception as error:
            raise _translate_azure_error(self._subscription_id, error) from error

    def _user_virtual_machines(self) -> Iterable[Any]:
        for name in self._user_resource_groups():
            for vm in self._compute_client.virtual_machines.list(name):
                yield self._compute_client.virtual_machines.get(
                    name, vm.name, expand="instanceView"
                )

    def _load_skus_for_location(self, location: str) -> None:
        normalized_location = location.lower()
        if normalized_location in self._loaded_sku_locations:
            return

        skus = self._compute_client.resource_skus.list(
            filter=f"location eq '{location}'"
        )
        for sku in skus:
            resource_type = _optional_non_empty_string(
                getattr(sku, "resource_type", None)
            )
            sku_name = _optional_non_empty_string(getattr(sku, "name", None))
            if (
                resource_type is None
                or resource_type.lower() != "virtualmachines"
                or sku_name is None
            ):
                continue
            locations = {
                item.lower()
                for item in (
                    _optional_non_empty_string(value)
                    for value in (getattr(sku, "locations", None) or ())
                )
                if item is not None
            }
            if locations and normalized_location not in locations:
                continue

            capabilities = _sku_capabilities(sku)
            vcpus = _capability_int(
                capabilities,
                "vCPUsAvailable",
                "vCPUs",
            )
            self._sku_cache[(normalized_location, sku_name.lower())] = (
                vcpus * 1000 if vcpus is not None else None,
                _memory_mib(capabilities),
                _architecture(capabilities),
            )

        self._loaded_sku_locations.add(normalized_location)

    def _machine_capacity(
        self,
        *,
        location: str,
        machine_type: str,
    ) -> tuple[int | None, int | None, Architecture | None]:
        try:
            self._load_skus_for_location(location)
        except Exception as error:
            if self._user_credential is None or not isinstance(
                _translate_azure_error(self._subscription_id, error), AzurePermissionError
            ):
                raise
            self._loaded_sku_locations.add(location.lower())
        return self._sku_cache.get(
            (location.lower(), machine_type.lower()),
            (None, None, None),
        )

    def _ip_addresses(self, vm: object) -> tuple[str | None, str | None]:
        network_profile = getattr(vm, "network_profile", None)
        nic_references = _primary_first(
            getattr(network_profile, "network_interfaces", None)
        )
        private_ip = None
        public_ip = None

        for nic_reference in nic_references:
            nic_id = getattr(nic_reference, "id", None)
            nic_resource_group = _resource_id_component(
                nic_id,
                "resourceGroups",
            )
            nic_name = _resource_id_component(nic_id, "networkInterfaces")
            nic = self._network_client.network_interfaces.get(
                resource_group_name=nic_resource_group,
                network_interface_name=nic_name,
            )

            for ip_configuration in _primary_first(
                getattr(nic, "ip_configurations", None)
            ):
                if private_ip is None:
                    private_ip = _optional_non_empty_string(
                        getattr(ip_configuration, "private_ip_address", None)
                    )

                public_ip_reference = getattr(
                    ip_configuration,
                    "public_ip_address",
                    None,
                )
                public_ip_id = getattr(public_ip_reference, "id", None)
                if public_ip is not None or public_ip_id is None:
                    continue

                public_ip_resource_group = _resource_id_component(
                    public_ip_id,
                    "resourceGroups",
                )
                public_ip_name = _resource_id_component(
                    public_ip_id,
                    "publicIPAddresses",
                )
                public_ip_resource = (
                    self._network_client.public_ip_addresses.get(
                        resource_group_name=public_ip_resource_group,
                        public_ip_address_name=public_ip_name,
                    )
                )
                public_ip = _optional_non_empty_string(
                    getattr(public_ip_resource, "ip_address", None)
                )

            if private_ip is not None and public_ip is not None:
                break

        return private_ip, public_ip

    def list_instances(self) -> tuple[ProviderInstanceSummary, ...]:
        summaries: list[ProviderInstanceSummary] = []

        try:
            if self._user_credential is not None:
                status_by_vm_key = {}
                virtual_machines = self._user_virtual_machines()
            else:
                status_by_vm_key = {
                    key: vm
                    for vm in self._compute_client.virtual_machines.list_all(status_only="true")
                    if (key := _vm_lookup_key(vm)) is not None
                }
                virtual_machines = self._compute_client.virtual_machines.list_all()
            for vm in virtual_machines:
                try:
                    internal_ip, external_ip = self._ip_addresses(vm)
                except Exception as error:
                    if self._user_credential is None or not isinstance(
                        _translate_azure_error(self._subscription_id, error), AzurePermissionError
                    ):
                        raise
                    internal_ip, external_ip = None, None
                location = _require_non_empty_string(
                    "vm.location",
                    getattr(vm, "location", None),
                )
                machine_type = _machine_type(vm)
                status_vm = status_by_vm_key.get(_vm_lookup_key(vm), vm)
                (
                    capacity_cpu_millicores,
                    capacity_memory_mib,
                    architecture,
                ) = self._machine_capacity(
                    location=location,
                    machine_type=machine_type,
                )
                summaries.append(
                    ProviderInstanceSummary(
                        provider_scope_id=self._subscription_id,
                        provider_instance_id=_require_non_empty_string(
                            "vm.vm_id",
                            getattr(vm, "vm_id", None),
                        ),
                        name=_require_non_empty_string(
                            "vm.name",
                            getattr(vm, "name", None),
                        ),
                        region=location,
                        zone=_availability_zone(vm),
                        status=_power_state(status_vm),
                        machine_type=machine_type,
                        internal_ip=internal_ip,
                        external_ip=external_ip,
                        architecture=architecture,
                        provider_capacity_cpu_millicores=(
                            capacity_cpu_millicores
                        ),
                        provider_capacity_memory_mib=capacity_memory_mib,
                        provider_capacity_storage_mib=_attached_storage_mib(vm),
                    )
                )
        except AzureAdapterError:
            raise
        except Exception as error:
            raise _translate_azure_error(
                self._subscription_id,
                error,
            ) from error

        return tuple(summaries)
