from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import ResourceTargetTable
from app.domain.enums import Provider
from app.schemas.provider_account import ErrorResponse
from app.schemas.resource_target import (
    AdminResourceTargetListResponse,
    AdminResourceTargetResponse,
    ResourceCapacityResponse,
    ResourceRuntimeResponse,
)
from app.services.resource_target_service import ResourceTargetService


router = APIRouter(
    prefix="/v1/admin/resource-targets",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


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


def _resource_response(
    resource: ResourceTargetTable,
) -> AdminResourceTargetResponse:
    account = resource.account
    allocatable = _capacity(
        cpu_millicores=resource.allocatable_cpu_millicores,
        memory_mib=resource.allocatable_memory_mib,
        storage_mib=resource.allocatable_ephemeral_storage_mib,
    )
    return AdminResourceTargetResponse(
        resource_target_id=resource.resource_target_id,
        provider=account.provider,
        account_id=account.account_id,
        account_display_name=account.display_name,
        external_account_id=account.external_account_id,
        scope_id=resource.provider_scope_id,
        instance_id=resource.provider_instance_id,
        name=resource.instance_name,
        region=resource.region,
        zone=resource.zone,
        status=resource.provider_instance_state,
        machine_type=resource.provider_machine_type,
        architecture=resource.architecture,
        internal_ip=resource.private_ip,
        external_ip=resource.public_ip,
        provider_capacity=_capacity(
            cpu_millicores=resource.provider_capacity_cpu_millicores,
            memory_mib=resource.provider_capacity_memory_mib,
            storage_mib=resource.provider_capacity_storage_mib,
        ),
        allocatable_capacity=allocatable,
        runtime=ResourceRuntimeResponse(
            type=resource.runtime_type,
            target_id=resource.target_id,
            ready=resource.ready,
        ),
        enabled=resource.enabled,
        observed_at=resource.observed_at,
        last_seen_at=resource.last_seen_at,
        retired_at=resource.retired_at,
    )


@router.get(
    "",
    response_model=AdminResourceTargetListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def list_resource_targets(
    provider: Provider | None = None,
    account_id: UUID | None = None,
    include_retired: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_db_session),
) -> AdminResourceTargetListResponse | Response:
    try:
        outcome = ResourceTargetService(session).list_resources(
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
            "The resource target list could not be loaded.",
        )

    return AdminResourceTargetListResponse(
        total=outcome.total,
        limit=limit,
        offset=offset,
        items=[_resource_response(item) for item in outcome.resources],
    )
