from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.adapters.gcp import GcpAdapterError, GcpDependencyError
from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import ResourceTargetTable, RuntimeContainerTable
from app.domain.enums import Provider
from app.schemas.provider_account import ErrorResponse
from app.schemas.resource_target import (
    AdminResourceTargetListResponse,
    AdminResourceTargetResponse,
    AdminResourceTargetUpdateRequest,
    AdminRuntimeContainerListResponse,
    AdminRuntimeContainerResponse,
    ResourceCapacityResponse,
    ResourceRuntimeResponse,
    ResourceUsageResponse,
    VmPowerRequest,
    VmPowerResponse,
)
from app.services.gcp_vm_power_service import GcpVmPowerService, VmPowerError
from app.services.resource_target_service import (
    ResourceTargetNotEligibleError,
    ResourceTargetNotFoundError,
    ResourceTargetService,
)
from app.services.scheduler_settings import SchedulerConfigurationError


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
        os_type=resource.os_type,
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
            observed_at=resource.runtime_observed_at,
            last_seen_at=resource.runtime_last_seen_at,
            usage=ResourceUsageResponse(
                cpu_millicores=resource.runtime_cpu_usage_millicores,
                memory_mib=resource.runtime_memory_usage_mib,
            ),
        ),
        account_enabled=account.enabled,
        enabled=resource.enabled,
        observed_at=resource.observed_at,
        last_seen_at=resource.last_seen_at,
        retired_at=resource.retired_at,
    )


def _container_response(
    container: RuntimeContainerTable,
) -> AdminRuntimeContainerResponse:
    return AdminRuntimeContainerResponse(
        container_id=container.container_id,
        container_name=container.container_name,
        pod_name=container.pod_name,
        namespace=container.namespace,
        status=container.status,
        cpu_request_millicores=container.cpu_request_millicores,
        memory_request_mib=container.memory_request_mib,
        ephemeral_storage_request_mib=(
            container.ephemeral_storage_request_mib
        ),
        cpu_usage_millicores=container.cpu_usage_millicores,
        memory_usage_mib=container.memory_usage_mib,
        storage_usage_mib=container.storage_usage_mib,
        observed_at=container.observed_at,
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


@router.post(
    "/{resource_target_id}/power",
    response_model=VmPowerResponse,
    status_code=202,
    summary="Start or stop a tagged GCP problem VM",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def set_vm_power(
    resource_target_id: UUID,
    request: VmPowerRequest,
    session: Session = Depends(get_db_session),
) -> VmPowerResponse | Response:
    try:
        result = GcpVmPowerService(session).start_or_stop(resource_target_id, request.action)
    except VmPowerError as error:
        return _error_response(404 if error.code == "RESOURCE_TARGET_NOT_FOUND" else 409, error.code, error.message)
    except GcpAdapterError as error:
        return _error_response(503 if isinstance(error, GcpDependencyError) else 502, error.error_code, error.public_message)
    except SQLAlchemyError:
        session.rollback()
        return _error_response(500, "DATABASE_ERROR", "The VM power state could not be updated.")
    return VmPowerResponse(status=result.status, vm_status=result.vm_status, operation_name=result.operation_name)


@router.get(
    "/{resource_target_id}/power-operations/{operation_name}",
    response_model=VmPowerResponse,
    summary="Check a GCP problem VM power operation",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def get_vm_power_operation(
    resource_target_id: UUID,
    operation_name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")],
    action: Literal["START", "STOP"],
    session: Session = Depends(get_db_session),
) -> VmPowerResponse | Response:
    try:
        result = GcpVmPowerService(session).operation_status(resource_target_id, action, operation_name)
    except VmPowerError as error:
        return _error_response(404 if error.code == "RESOURCE_TARGET_NOT_FOUND" else 409, error.code, error.message)
    except GcpAdapterError as error:
        return _error_response(503 if isinstance(error, GcpDependencyError) else 502, error.error_code, error.public_message)
    except SQLAlchemyError:
        session.rollback()
        return _error_response(500, "DATABASE_ERROR", "The VM power state could not be updated.")
    return VmPowerResponse(status=result.status, vm_status=result.vm_status, operation_name=result.operation_name)


@router.patch(
    "/{resource_target_id}",
    response_model=AdminResourceTargetResponse,
    summary="Enable or disable a VM for Scheduler candidates",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def update_resource_target(
    resource_target_id: UUID,
    request: AdminResourceTargetUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminResourceTargetResponse | Response:
    try:
        resource = ResourceTargetService(session).set_enabled(
            resource_target_id,
            enabled=request.enabled,
        )
    except ResourceTargetNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "RESOURCE_TARGET_NOT_FOUND",
            "The resource target was not found.",
        )
    except ResourceTargetNotEligibleError as error:
        return _error_response(409, error.code, error.message)
    except SchedulerConfigurationError:
        return _error_response(
            503,
            "SCHEDULER_NOT_CONFIGURED",
            "Scheduler capacity settings are not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The resource target could not be updated.",
        )
    return _resource_response(resource)


@router.get(
    "/{resource_target_id}/containers",
    response_model=AdminRuntimeContainerListResponse,
    summary="List the current containers observed on a VM",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def list_resource_target_containers(
    resource_target_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminRuntimeContainerListResponse | Response:
    try:
        outcome = ResourceTargetService(session).list_containers(
            resource_target_id
        )
    except ResourceTargetNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "RESOURCE_TARGET_NOT_FOUND",
            "The resource target was not found.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The runtime container list could not be loaded.",
        )

    return AdminRuntimeContainerListResponse(
        resource_target_id=outcome.resource_target_id,
        observed_at=outcome.observed_at,
        total=len(outcome.containers),
        items=[_container_response(item) for item in outcome.containers],
    )
