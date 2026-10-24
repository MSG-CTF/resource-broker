from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import WindowsWorkerPoolTable, WindowsWorkerTable
from app.schemas.provider_account import ErrorResponse
from app.schemas.windows_worker import (
    WindowsWorkerPoolCreateRequest,
    WindowsWorkerPoolListResponse,
    WindowsWorkerPoolResponse,
    WindowsWorkerResponse,
)
from app.services.windows_worker_service import (
    DuplicateWindowsWorkerPoolError,
    WindowsWorkerService,
)


router = APIRouter(
    prefix="/v1/admin/windows-worker-pools",
    tags=["admin-windows-workers"],
    dependencies=[Depends(require_admin)],
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


def _worker_response(worker: WindowsWorkerTable) -> WindowsWorkerResponse:
    return WindowsWorkerResponse(
        worker_id=worker.worker_id,
        instance_name=worker.instance_name,
        instance_id=worker.instance_id,
        private_ip=worker.private_ip,
        status=worker.status,
        ready_at=worker.ready_at,
        last_seen_at=worker.last_seen_at,
        error_code=worker.error_code,
        error_message=worker.error_message,
    )


def _pool_response(pool: WindowsWorkerPoolTable) -> WindowsWorkerPoolResponse:
    workers = sorted(
        pool.workers,
        key=lambda item: (item.instance_name, str(item.worker_id)),
    )
    return WindowsWorkerPoolResponse(
        pool_id=pool.pool_id,
        project_id=pool.project_id,
        zone=pool.zone,
        managed_instance_group=pool.managed_instance_group,
        display_name=pool.display_name,
        enabled=pool.enabled,
        last_synced_at=pool.last_synced_at,
        error_code=pool.error_code,
        error_message=pool.error_message,
        workers=[_worker_response(worker) for worker in workers],
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )


@router.post(
    "",
    response_model=WindowsWorkerPoolResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def create_windows_worker_pool(
    request: WindowsWorkerPoolCreateRequest,
    session: Session = Depends(get_db_session),
) -> WindowsWorkerPoolResponse | Response:
    try:
        pool = WindowsWorkerService(session).create_pool(
            project_id=request.project_id,
            zone=request.zone,
            managed_instance_group=request.managed_instance_group,
            display_name=request.display_name,
        )
        return _pool_response(pool)
    except DuplicateWindowsWorkerPoolError:
        return _error(
            409,
            "WINDOWS_WORKER_POOL_ALREADY_EXISTS",
            "This Windows worker MIG is already registered.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The Windows worker pool could not be created.",
        )


@router.get(
    "",
    response_model=WindowsWorkerPoolListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def list_windows_worker_pools(
    session: Session = Depends(get_db_session),
) -> WindowsWorkerPoolListResponse | Response:
    try:
        pools = WindowsWorkerService(session).list_pools()
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The Windows worker pools could not be loaded.",
        )
    return WindowsWorkerPoolListResponse(
        items=[_pool_response(pool) for pool in pools]
    )
