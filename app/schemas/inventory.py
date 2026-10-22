from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Architecture, Provider, ReservationStatus
from app.schemas.resource_target import ResourceCapacityResponse, ResourceUsageResponse


class InventorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainerStorageUsageResponse(InventorySchema):
    observed_mib: int | None = Field(default=None, ge=0)
    observed_container_count: int = Field(ge=0)
    total_container_count: int = Field(ge=0)
    complete: bool


class AllocationResources(InventorySchema):
    cpu_millicores: int | None = Field(ge=0)
    memory_mib: int | None = Field(ge=0)


class InventoryAllocation(InventorySchema):
    reservation_id: UUID
    instance_id: UUID | None
    challenge_id: UUID
    team_id: UUID
    reservation_status: ReservationStatus
    expires_at: datetime | None
    resources: AllocationResources


class ResourceAllocationSnapshot(InventorySchema):
    collected_at: datetime
    complete: bool
    allocatable: AllocationResources
    other_reserved: AllocationResources
    allocations: list[InventoryAllocation]


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
    resource_allocation: ResourceAllocationSnapshot | None = None


class InventoryResourceTargetListResponse(InventorySchema):
    generated_at: datetime
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
    items: list[InventoryResourceTargetResponse]
