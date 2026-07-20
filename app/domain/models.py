from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.domain.enums import (
    Architecture,
    CostStatus,
    CredentialStatus,
    Currency,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
    QuotaStatus,
    ReasonCode,
    RiskLevel,
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


def _require_non_negative_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")


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


@dataclass(frozen=True, slots=True)
class CandidateCapacity:
    available_cpu_millicores: int
    available_memory_mib: int
    available_ephemeral_storage_mib: int
    fit_count: int

    def __post_init__(self) -> None:
        _require_non_negative_int(
            "available_cpu_millicores",
            self.available_cpu_millicores,
        )
        _require_non_negative_int(
            "available_memory_mib",
            self.available_memory_mib,
        )
        _require_non_negative_int(
            "available_ephemeral_storage_mib",
            self.available_ephemeral_storage_mib,
        )
        _require_non_negative_int("fit_count", self.fit_count)


#이놈 비용 계산 어떻게 할지 정해야 하는데.. 개인적으로 추가비용이 어떤 상황에서 생길지 잘 모르겠네
@dataclass(frozen=True, slots=True)
class CandidateCostEstimate:
    status: CostStatus
    estimated_request_cost: Decimal
    currency: Currency
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_enum("status", self.status, CostStatus)
        _require_non_negative_decimal(
            "estimated_request_cost",
            self.estimated_request_cost,
        )
        _require_enum("currency", self.currency, Currency)
        _require_aware_datetime("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    candidate_id: str
    target: ResourceTarget
    capacity: CandidateCapacity
    cost_estimate: CandidateCostEstimate
    risk: RiskLevel
    reason_codes: tuple[ReasonCode, ...]
    observed_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string("candidate_id", self.candidate_id)
        if not isinstance(self.target, ResourceTarget):
            raise TypeError("target must be a ResourceTarget")
        if not isinstance(self.capacity, CandidateCapacity):
            raise TypeError("capacity must be a CandidateCapacity")
        if not isinstance(self.cost_estimate, CandidateCostEstimate):
            raise TypeError("cost_estimate must be a CandidateCostEstimate")
        _require_enum("risk", self.risk, RiskLevel)
        if not isinstance(self.reason_codes, tuple):
            raise TypeError("reason_codes must be a tuple")
        for reason_code in self.reason_codes:
            _require_enum("reason_codes item", reason_code, ReasonCode)
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("valid_until", self.valid_until)
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be later than observed_at")
