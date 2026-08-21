from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.domain.enums import Architecture


NonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]


class RequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceProfile(RequestSchema):
    cpu_millicores: int = Field(gt=0, strict=True)
    memory_mib: int = Field(gt=0, strict=True)
    ephemeral_storage_mib: int = Field(gt=0, strict=True)
    architecture: Architecture


class CandidateQueryRequest(RequestSchema):
    request_id: NonEmptyString
    requested_at: AwareDatetime
    team_id: UUID
    challenge_id: UUID
    instance_id: NonEmptyString
    resource_profile: ResourceProfile
