from dataclasses import dataclass
from typing import Protocol

from app.domain.enums import Architecture


def _require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty or whitespace")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")


def _require_optional_non_empty_string(name: str, value: object) -> None:
    if value is not None:
        _require_non_empty_string(name, value)


def _require_optional_non_negative_int(name: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")


@dataclass(frozen=True, slots=True)
class ProviderInstanceSummary:
    """Provider-neutral VM data returned by a cloud adapter."""

    provider_scope_id: str
    provider_instance_id: str
    name: str
    region: str
    zone: str | None
    status: str
    machine_type: str
    internal_ip: str | None
    external_ip: str | None
    architecture: Architecture | None = None
    provider_capacity_cpu_millicores: int | None = None
    provider_capacity_memory_mib: int | None = None
    provider_capacity_storage_mib: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(
            "provider_scope_id",
            self.provider_scope_id,
        )
        _require_non_empty_string(
            "provider_instance_id",
            self.provider_instance_id,
        )
        _require_non_empty_string("name", self.name)
        _require_non_empty_string("region", self.region)
        _require_optional_non_empty_string("zone", self.zone)
        _require_non_empty_string("status", self.status)
        _require_non_empty_string("machine_type", self.machine_type)
        _require_optional_non_empty_string("internal_ip", self.internal_ip)
        _require_optional_non_empty_string("external_ip", self.external_ip)
        if self.architecture is not None and not isinstance(
            self.architecture,
            Architecture,
        ):
            raise TypeError("architecture must be an Architecture")
        _require_optional_non_negative_int(
            "provider_capacity_cpu_millicores",
            self.provider_capacity_cpu_millicores,
        )
        _require_optional_non_negative_int(
            "provider_capacity_memory_mib",
            self.provider_capacity_memory_mib,
        )
        _require_optional_non_negative_int(
            "provider_capacity_storage_mib",
            self.provider_capacity_storage_mib,
        )


class ProviderAdapter(Protocol):
    def list_instances(self) -> tuple[ProviderInstanceSummary, ...]:
        """Return VMs normalized into the broker's common discovery shape."""
        raise NotImplementedError
