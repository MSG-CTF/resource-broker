from dataclasses import dataclass
from typing import Protocol


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


class ProviderAdapter(Protocol):
    def list_instances(self) -> tuple[ProviderInstanceSummary, ...]:
        """Return VMs normalized into the broker's common discovery shape."""
        raise NotImplementedError
