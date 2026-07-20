from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    model_validator,
)

from app.domain.enums import (
    Architecture,
    CandidateQueryStatus,
    CostStatus,
    Currency,
    Provider,
    ReasonCode,
    RiskLevel,
    RuntimeType,
)


NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


def validate_json_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Input must be a JSON number")
    return value


EstimatedRequestCost = Annotated[
    Decimal,
    BeforeValidator(validate_json_number),
    Field(ge=0, allow_inf_nan=False),
]


class ResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeTarget(ResponseSchema):
    type: RuntimeType
    target_id: NonEmptyString


class AvailableCapacity(ResponseSchema):
    available_cpu_millicores: int = Field(ge=0, strict=True)
    available_memory_mib: int = Field(ge=0, strict=True)
    available_ephemeral_storage_mib: int = Field(ge=0, strict=True)
    fit_count: int = Field(ge=0, strict=True)


class CostEstimate(ResponseSchema):
    status: CostStatus
    estimated_request_cost: EstimatedRequestCost
    currency: Currency
    observed_at: AwareDatetime

    @field_serializer("estimated_request_cost", when_used="json")
    def serialize_estimated_request_cost(self, value: Decimal) -> float:
        return float(value)


class Candidate(ResponseSchema):
    candidate_id: NonEmptyString
    provider: Provider
    account_id: NonEmptyString
    region: NonEmptyString
    runtime: RuntimeTarget
    architecture: Architecture
    capacity: AvailableCapacity
    cost_estimate: CostEstimate
    risk: RiskLevel
    reason_codes: list[ReasonCode]
    observed_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validate_validity_window(self) -> Self:
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be later than observed_at")
        return self


class CandidateQueryResponse(ResponseSchema):
    request_id: NonEmptyString
    generated_at: AwareDatetime
    status: CandidateQueryStatus
    reason_codes: list[ReasonCode]
    candidates: list[Candidate]
