from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.domain.enums import Architecture, ReleaseReason, ReservationStatus
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


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReservationCreateRequest(ApiSchema):
    request_id: NonEmptyString
    requested_at: AwareDatetime
    instance_id: NonEmptyString
    candidate_id: UUID
    team_id: UUID
    challenge_id: UUID
    architecture: Architecture
    resource_profile: ResourceProfile


class ReservationMutationRequest(ApiSchema):
    request_id: NonEmptyString
    requested_at: AwareDatetime
    instance_id: NonEmptyString
    reservation_id: UUID


class ReservationCommitRequest(ReservationMutationRequest):
    runtime_workload_id: NonEmptyString
    resource_profile: ResourceProfile


class ReservationReleaseRequest(ReservationMutationRequest):
    release_reason: ReleaseReason


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
    team_id: UUID
    challenge_id: UUID
    instance_id: NonEmptyString
    resource_profile: ReservationCapacity
    status: ReservationStatus
    expires_at: datetime | None
    created_at: datetime
    committed_at: datetime | None
    released_at: datetime | None
    runtime_workload_id: NonEmptyString | None
    deployed_resource_profile: ResourceProfile | None
    release_reason: ReleaseReason | None
    updated_at: datetime
