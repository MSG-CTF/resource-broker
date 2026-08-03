from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import RuntimeType


RuntimeIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]
ContainerStatus = Literal["RUNNING", "WAITING", "TERMINATED", "UNKNOWN"]


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCapacity(ApiSchema):
    cpu_millicores: int = Field(ge=0, strict=True)
    memory_mib: int = Field(ge=0, strict=True)
    ephemeral_storage_mib: int = Field(ge=0, strict=True)


class AgentRuntimeObservation(ApiSchema):
    type: Literal[RuntimeType.KUBERNETES]
    target_id: RuntimeIdentifier
    ready: bool


class AgentContainerObservation(ApiSchema):
    container_id: RuntimeIdentifier
    container_name: RuntimeIdentifier
    pod_name: RuntimeIdentifier | None = None
    namespace: RuntimeIdentifier | None = None
    status: ContainerStatus
    cpu_usage_millicores: int | None = Field(default=None, ge=0, strict=True)
    memory_usage_mib: int | None = Field(default=None, ge=0, strict=True)
    storage_usage_mib: int | None = Field(default=None, ge=0, strict=True)


class AgentObservationRequest(ApiSchema):
    resource_target_id: UUID
    observed_at: datetime
    snapshot_complete: bool
    runtime: AgentRuntimeObservation
    node_allocatable: AgentCapacity
    allocated_requests: AgentCapacity
    containers: list[AgentContainerObservation] = Field(max_length=10_000)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_unique_container_ids(self) -> "AgentObservationRequest":
        container_ids = [item.container_id for item in self.containers]
        if len(container_ids) != len(set(container_ids)):
            raise ValueError("containers must not contain duplicate container_id values")
        return self


class AgentObservationResponse(ApiSchema):
    resource_target_id: UUID
    observed_at: datetime
    accepted_at: datetime
    snapshot_complete: bool
    containers_received: int = Field(ge=0)
    containers_created: int = Field(ge=0)
    containers_updated: int = Field(ge=0)
    containers_deleted: int = Field(ge=0)
    allocatable_capacity: AgentCapacity
