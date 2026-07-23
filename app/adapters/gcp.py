from collections.abc import Callable, Iterable
from typing import Any, Protocol

from app.adapters.base import ProviderInstanceSummary


class GcpInstancesClient(Protocol):
    def aggregated_list(self, *, request: object) -> Iterable[tuple[str, Any]]:
        raise NotImplementedError


GcpRequestFactory = Callable[..., object]


class GcpAdapterError(RuntimeError):
    error_code = "GCP_API_ERROR"
    public_message = "GCP API request failed."

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(self.public_message)


class GcpDependencyError(GcpAdapterError):
    error_code = "GCP_SDK_NOT_INSTALLED"
    public_message = "The GCP SDK dependency is not installed."


class GcpAuthenticationError(GcpAdapterError):
    error_code = "GCP_AUTHENTICATION_FAILED"
    public_message = "GCP authentication failed."


class GcpPermissionError(GcpAdapterError):
    error_code = "GCP_PERMISSION_DENIED"
    public_message = "The broker service account cannot access this project."


class GcpProjectNotFoundError(GcpPermissionError):
    error_code = "GCP_PROJECT_NOT_FOUND"
    public_message = "The GCP project was not found or is not accessible."


class GcpApiUnavailableError(GcpAdapterError):
    error_code = "GCP_API_UNAVAILABLE"
    public_message = "The GCP Compute API is unavailable."


def _require_non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty or whitespace")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    return value


def _last_resource_segment(value: object, field_name: str) -> str:
    resource_name = _require_non_empty_string(field_name, value)
    return resource_name.rsplit("/", maxsplit=1)[-1]


def _region_from_zone(zone: str) -> str:
    region, separator, suffix = zone.rpartition("-")
    if not separator or not region or not suffix:
        raise ValueError("zone must be a valid GCP zone name")
    return region


def _first_non_empty_attribute(items: object, attribute: str) -> str | None:
    for item in items or ():
        value = getattr(item, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _instance_ip_addresses(instance: object) -> tuple[str | None, str | None]:
    interfaces = getattr(instance, "network_interfaces", None) or ()
    private_ip = _first_non_empty_attribute(interfaces, "network_i_p")

    public_ip = None
    for interface in interfaces:
        public_ip = _first_non_empty_attribute(
            getattr(interface, "access_configs", None),
            "nat_i_p",
        )
        if public_ip is not None:
            break

    return private_ip, public_ip


def _translate_google_error(
    project_id: str,
    error: Exception,
) -> GcpAdapterError:
    try:
        from google.api_core import exceptions as api_exceptions
        from google.auth import exceptions as auth_exceptions
    except ModuleNotFoundError:
        return GcpDependencyError(project_id)

    if isinstance(error, auth_exceptions.GoogleAuthError):
        return GcpAuthenticationError(project_id)
    if isinstance(error, api_exceptions.Unauthenticated):
        return GcpAuthenticationError(project_id)
    if isinstance(error, api_exceptions.NotFound):
        return GcpProjectNotFoundError(project_id)
    if isinstance(error, api_exceptions.Forbidden):
        message = str(error).lower()
        if "has not been used" in message or "is disabled" in message:
            return GcpApiUnavailableError(project_id)
        return GcpPermissionError(project_id)
    if isinstance(
        error,
        (
            api_exceptions.BadGateway,
            api_exceptions.GatewayTimeout,
            api_exceptions.InternalServerError,
            api_exceptions.ServiceUnavailable,
            api_exceptions.TooManyRequests,
        ),
    ):
        return GcpApiUnavailableError(project_id)
    if isinstance(error, api_exceptions.GoogleAPICallError):
        return GcpApiUnavailableError(project_id)
    return GcpAdapterError(project_id)


class GcpAdapter:
    def __init__(
        self,
        project_id: str,
        instances_client: GcpInstancesClient,
        request_factory: GcpRequestFactory,
    ) -> None:
        self._project_id = _require_non_empty_string(
            "project_id",
            project_id,
        )
        self._instances_client = instances_client
        self._request_factory = request_factory

    @classmethod
    def from_adc(cls, project_id: str) -> "GcpAdapter":
        try:
            from google.cloud import compute_v1
        except ModuleNotFoundError as error:
            raise GcpDependencyError(project_id) from error

        try:
            return cls(
                project_id=project_id,
                instances_client=compute_v1.InstancesClient(),
                request_factory=compute_v1.AggregatedListInstancesRequest,
            )
        except Exception as error:
            raise _translate_google_error(project_id, error) from error

    def list_instances(self) -> tuple[ProviderInstanceSummary, ...]:
        request = self._request_factory(
            project=self._project_id,
            max_results=50,
        )
        summaries: list[ProviderInstanceSummary] = []

        try:
            pages = self._instances_client.aggregated_list(request=request)
            for zone_scope, scoped_instances in pages:
                zone = _last_resource_segment(zone_scope, "zone_scope")
                region = _region_from_zone(zone)
                instances = getattr(scoped_instances, "instances", None) or ()

                for instance in instances:
                    internal_ip, external_ip = _instance_ip_addresses(instance)
                    summaries.append(
                        ProviderInstanceSummary(
                            provider_scope_id=self._project_id,
                            provider_instance_id=_require_non_empty_string(
                                "instance.id",
                                str(instance.id),
                            ),
                            name=_require_non_empty_string(
                                "instance.name",
                                instance.name,
                            ),
                            region=region,
                            zone=zone,
                            status=_require_non_empty_string(
                                "instance.status",
                                instance.status,
                            ),
                            machine_type=_last_resource_segment(
                                instance.machine_type,
                                "instance.machine_type",
                            ),
                            internal_ip=internal_ip,
                            external_ip=external_ip,
                        )
                    )
        except GcpAdapterError:
            raise
        except Exception as error:
            raise _translate_google_error(self._project_id, error) from error

        return tuple(summaries)
