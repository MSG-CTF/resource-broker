from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.inventory_auth import require_inventory
from app.db.session import get_db_session
from app.db.tables import ResourceTargetTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import ContainerStorageUsage
from app.repositories.reservations import ReservedCapacity
from app.schemas.inventory import (
    ContainerStorageUsageResponse,
    InventoryResourceTargetListResponse,
    InventoryResourceTargetResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.schemas.resource_target import ResourceCapacityResponse, ResourceUsageResponse
from app.services.inventory_service import InventoryService


router = APIRouter(
    prefix="/v1/inventory/resource-targets",
    tags=["inventory"],
    dependencies=[Depends(require_inventory)],
)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _capacity(
    *,
    cpu_millicores: int | None,
    memory_mib: int | None,
    storage_mib: int | None,
) -> ResourceCapacityResponse:
    return ResourceCapacityResponse(
        cpu_millicores=cpu_millicores,
        memory_mib=memory_mib,
        storage_mib=storage_mib,
    )


def _remaining(value: int | None, deduction: int) -> int | None:
    return max(value - deduction, 0) if value is not None else None


def _resource_response(
    resource: ResourceTargetTable,
    reservations: ReservedCapacity | None,
    container_storage: ContainerStorageUsage | None,
) -> InventoryResourceTargetResponse:
    reserved = reservations or ReservedCapacity(0, 0, 0)
    observed_container_count = (
        container_storage.observed_container_count if container_storage else 0
    )
    total_container_count = (
        container_storage.total_container_count if container_storage else 0
    )
    current_observation_container_count = (
        container_storage.current_observation_container_count
        if container_storage
        else 0
    )
    storage_observed = resource.runtime_observed_at is not None
    if container_storage and observed_container_count > 0:
        observed_storage_mib = container_storage.observed_mib
    elif storage_observed and total_container_count == 0:
        observed_storage_mib = 0
    else:
        observed_storage_mib = None

    return InventoryResourceTargetResponse(
        resource_target_id=resource.resource_target_id,
        name=resource.instance_name,
        provider=resource.account.provider,
        account_id=resource.account_id,
        scope_id=resource.provider_scope_id,
        instance_id=resource.provider_instance_id,
        region=resource.region,
        zone=resource.zone,
        status=resource.provider_instance_state,
        machine_type=resource.provider_machine_type,
        architecture=resource.architecture,
        public_ip=resource.public_ip,
        provider_capacity=_capacity(
            cpu_millicores=resource.provider_capacity_cpu_millicores,
            memory_mib=resource.provider_capacity_memory_mib,
            storage_mib=resource.provider_capacity_storage_mib,
        ),
        runtime_usage=ResourceUsageResponse(
            cpu_millicores=resource.runtime_cpu_usage_millicores,
            memory_mib=resource.runtime_memory_usage_mib,
        ),
        container_storage_usage=ContainerStorageUsageResponse(
            observed_mib=observed_storage_mib,
            observed_container_count=observed_container_count,
            total_container_count=total_container_count,
            complete=(
                storage_observed
                and observed_container_count == total_container_count
                and current_observation_container_count == total_container_count
            ),
        ),
        allocatable_capacity=_capacity(
            cpu_millicores=resource.allocatable_cpu_millicores,
            memory_mib=resource.allocatable_memory_mib,
            storage_mib=resource.allocatable_ephemeral_storage_mib,
        ),
        remaining_capacity=_capacity(
            cpu_millicores=_remaining(
                resource.allocatable_cpu_millicores,
                reserved.cpu_millicores,
            ),
            memory_mib=_remaining(
                resource.allocatable_memory_mib,
                reserved.memory_mib,
            ),
            storage_mib=_remaining(
                resource.allocatable_ephemeral_storage_mib,
                reserved.ephemeral_storage_mib,
            ),
        ),
        account_enabled=resource.account.enabled,
        enabled=resource.enabled,
        runtime_ready=resource.ready,
        provider_observed_at=resource.observed_at,
        runtime_observed_at=resource.runtime_observed_at,
        runtime_last_seen_at=resource.runtime_last_seen_at,
        retired_at=resource.retired_at,
    )


@router.get(
    "",
    response_model=InventoryResourceTargetListResponse,
    summary="List VM inventory with reservation-adjusted remaining capacity",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def list_inventory_resource_targets(
    provider: Provider | None = None,
    account_id: UUID | None = None,
    include_retired: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_db_session),
) -> InventoryResourceTargetListResponse | Response:
    try:
        outcome = InventoryService(session).list_resources(
            provider=provider,
            account_id=account_id,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The inventory could not be loaded.",
        )

    return InventoryResourceTargetListResponse(
        generated_at=outcome.generated_at,
        total=outcome.total,
        limit=limit,
        offset=offset,
        items=[
            _resource_response(
                resource,
                outcome.reservations_by_target.get(resource.resource_target_id),
                outcome.container_storage_by_target.get(resource.resource_target_id),
            )
            for resource in outcome.resources
        ],
    )
