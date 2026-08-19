from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.enums import Architecture, ReservationStatus
from app.schemas.candidate_request import ResourceProfile


NonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        strict=True,
    ),
]


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReservationCreateRequest(ApiSchema):
    idempotency_key: IdempotencyKey
    request_id: NonEmptyString
    candidate_id: UUID
    team_id: int = Field(gt=0, strict=True)
    challenge_id: int = Field(gt=0, strict=True)
    instance_id: NonEmptyString
    resource_profile: ResourceProfile


class ReservationCapacity(ApiSchema):
    cpu_millicores: int = Field(gt=0, strict=True)
    memory_mib: int = Field(gt=0, strict=True)
    ephemeral_storage_mib: int = Field(gt=0, strict=True)
    architecture: Architecture


class ReservationResponse(ApiSchema):
    reservation_id: UUID
    request_id: NonEmptyString
    candidate_id: UUID
    target_id: NonEmptyString
    team_id: int
    challenge_id: int
    instance_id: NonEmptyString
    resource_profile: ReservationCapacity
    status: ReservationStatus
    expires_at: datetime | None
    created_at: datetime
    committed_at: datetime | None
    released_at: datetime | None
    updated_at: datetime
