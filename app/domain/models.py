from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.domain.enums import (
    Architecture,
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
    QuotaStatus,
    RuntimeType,
)


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


def _require_non_negative_int(name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")


def _require_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")


def _require_enum(name: str, value: object, enum_type: type[Enum]) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{name} must be a {enum_type.__name__}")


def _require_aware_datetime(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


@dataclass(frozen=True, slots=True)
class ResourceTarget:
    provider: Provider
    account_id: str
    provider_instance_id: str
    region: str
    zone: str | None
    runtime_type: RuntimeType
    target_id: str
    architecture: Architecture
    allocatable_cpu_millicores: int
    allocatable_memory_mib: int
    allocatable_ephemeral_storage_mib: int
    enabled: bool
    ready: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_enum("provider", self.provider, Provider)
        _require_non_empty_string("account_id", self.account_id)
        _require_non_empty_string(
            "provider_instance_id",
            self.provider_instance_id,
        )
        _require_non_empty_string("region", self.region)
        _require_optional_non_empty_string("zone", self.zone)
        _require_enum("runtime_type", self.runtime_type, RuntimeType)
        _require_non_empty_string("target_id", self.target_id)
        _require_enum("architecture", self.architecture, Architecture)
        _require_non_negative_int(
            "allocatable_cpu_millicores",
            self.allocatable_cpu_millicores,
        )
        _require_non_negative_int(
            "allocatable_memory_mib",
            self.allocatable_memory_mib,
        )
        _require_non_negative_int(
            "allocatable_ephemeral_storage_mib",
            self.allocatable_ephemeral_storage_mib,
        )
        _require_bool("enabled", self.enabled)
        _require_bool("ready", self.ready)
        _require_aware_datetime("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class ProviderAccountStatus:
    provider: Provider
    account_id: str
    enabled: bool
    credential_status: CredentialStatus
    permission_status: PermissionStatus
    provider_api_status: ProviderApiStatus
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_enum("provider", self.provider, Provider)
        _require_non_empty_string("account_id", self.account_id)
        _require_bool("enabled", self.enabled)
        _require_enum(
            "credential_status",
            self.credential_status,
            CredentialStatus,
        )
        _require_enum(
            "permission_status",
            self.permission_status,
            PermissionStatus,
        )
        _require_enum(
            "provider_api_status",
            self.provider_api_status,
            ProviderApiStatus,
        )
        _require_aware_datetime("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    provider: Provider
    account_id: str
    region: str
    status: QuotaStatus
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_enum("provider", self.provider, Provider)
        _require_non_empty_string("account_id", self.account_id)
        _require_non_empty_string("region", self.region)
        _require_enum("status", self.status, QuotaStatus)
        _require_aware_datetime("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class ActiveResourceUsage:
    target_id: str
    cpu_millicores: int
    memory_mib: int
    ephemeral_storage_mib: int

    def __post_init__(self) -> None:
        _require_non_empty_string("target_id", self.target_id)
        _require_non_negative_int("cpu_millicores", self.cpu_millicores)
        _require_non_negative_int("memory_mib", self.memory_mib)
        _require_non_negative_int(
            "ephemeral_storage_mib",
            self.ephemeral_storage_mib,
        )
