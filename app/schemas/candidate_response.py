from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.enums import (
    Architecture,
    CandidateQueryStatus,
    Provider,
    RuntimeType,
)


NonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]


class ResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeTarget(ResponseSchema):
    type: RuntimeType
    target_id: NonEmptyString


class RemainingCapacity(ResponseSchema):
    cpu_millicores: int = Field(ge=0, strict=True)
    memory_mib: int = Field(ge=0, strict=True)
    ephemeral_storage_mib: int = Field(ge=0, strict=True)
    fit_count: int = Field(ge=0, strict=True)


class Candidate(ResponseSchema):
    candidate_id: UUID
    provider: Provider
    account_id: UUID
    region: NonEmptyString
    zone: NonEmptyString | None
    runtime: RuntimeTarget
    architecture: Architecture
    remaining_capacity: RemainingCapacity
    runtime_observed_at: datetime
    valid_until: datetime


class CandidateQueryResponse(ResponseSchema):
    request_id: NonEmptyString
    generated_at: datetime
    status: CandidateQueryStatus
    candidates: list[Candidate]
