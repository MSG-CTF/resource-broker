from typing import Annotated

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
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
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
    team_id: int = Field(gt=0, strict=True)
    challenge_id: int = Field(gt=0, strict=True)
    instance_id: NonEmptyString
    resource_profile: ResourceProfile
