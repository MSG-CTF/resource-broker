from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Architecture, Provider, RuntimeType


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceCapacityResponse(ApiSchema):
    cpu_millicores: int | None = Field(default=None, ge=0)
    memory_mib: int | None = Field(default=None, ge=0)
    storage_mib: int | None = Field(default=None, ge=0)


class ResourceRuntimeResponse(ApiSchema):
    type: RuntimeType | None
    target_id: str | None
    ready: bool
    observed_at: datetime | None
    last_seen_at: datetime | None


class AdminResourceTargetResponse(ApiSchema):
    resource_target_id: UUID
    provider: Provider
    account_id: UUID
    account_display_name: str | None
    external_account_id: str
    scope_id: str | None
    instance_id: str
    name: str | None
    region: str
    zone: str | None
    status: str | None
    machine_type: str | None
    architecture: Architecture | None
    internal_ip: str | None
    external_ip: str | None
    provider_capacity: ResourceCapacityResponse
    allocatable_capacity: ResourceCapacityResponse
    runtime: ResourceRuntimeResponse
    enabled: bool
    observed_at: datetime
    last_seen_at: datetime
    retired_at: datetime | None


class AdminResourceTargetListResponse(ApiSchema):
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
    items: list[AdminResourceTargetResponse]
