from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Architecture, Provider
from app.schemas.resource_target import ResourceCapacityResponse, ResourceUsageResponse


class InventorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainerStorageUsageResponse(InventorySchema):
    observed_mib: int | None = Field(default=None, ge=0)
    observed_container_count: int = Field(ge=0)
    total_container_count: int = Field(ge=0)
    complete: bool


class InventoryResourceTargetResponse(InventorySchema):
    resource_target_id: UUID
    name: str | None
    provider: Provider
    account_id: UUID
    scope_id: str | None
    instance_id: str
    region: str
    zone: str | None
    status: str | None
    machine_type: str | None
    architecture: Architecture | None
    public_ip: str | None
    provider_capacity: ResourceCapacityResponse
    runtime_usage: ResourceUsageResponse
    container_storage_usage: ContainerStorageUsageResponse
    allocatable_capacity: ResourceCapacityResponse
    remaining_capacity: ResourceCapacityResponse
    account_enabled: bool
    enabled: bool
    runtime_ready: bool
    provider_observed_at: datetime
    runtime_observed_at: datetime | None
    runtime_last_seen_at: datetime | None
    retired_at: datetime | None


class InventoryResourceTargetListResponse(InventorySchema):
    generated_at: datetime
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
    items: list[InventoryResourceTargetResponse]
