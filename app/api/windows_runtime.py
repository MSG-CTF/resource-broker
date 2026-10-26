from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.api.runtime_auth import require_runtime
from app.db.session import get_db_session
from app.schemas.provider_account import ErrorResponse
from app.schemas.windows_runtime import (
    WindowsVmAvailableListResponse,
    WindowsVmConnectionAdminResponse,
    WindowsVmConnectionResponse,
    WindowsVmConnectionUpdate,
    WindowsVmExecutionEvent,
    WindowsVmExecutionResponse,
)
from app.services.windows_runtime import WindowsRuntimeError, WindowsRuntimeService


router = APIRouter(
    prefix="/v1/runtime/windows-vms",
    tags=["runtime-windows-vms"],
    dependencies=[Depends(require_runtime)],
)
admin_router = APIRouter(
    prefix="/v1/admin/windows-vms",
    tags=["admin-windows-vms"],
    dependencies=[Depends(require_admin)],
)
errors = {code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 500, 503)}


def _error(session: Session, error: Exception):
    session.rollback()
    if isinstance(error, WindowsRuntimeError):
        code = error.code
        message = error.public_message
        if code in {"WINDOWS_VM_NOT_FOUND", "WINDOWS_EXECUTION_NOT_FOUND"}:
            status_code = 404
        elif code == "WINDOWS_CONNECTION_NOT_CONFIGURED":
            status_code = 503
        else:
            status_code = 409
    elif isinstance(error, IntegrityError):
        code, message, status_code = "WINDOWS_EXECUTION_CONFLICT", "The request conflicts with an existing execution or VM configuration.", 409
    else:
        code, message, status_code = "DATABASE_ERROR", "The Windows VM request could not be processed.", 500
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}}, headers={"Cache-Control": "no-store"})


def _admin_response(connection):
    return WindowsVmConnectionAdminResponse(**{name: getattr(connection, name) for name in WindowsVmConnectionAdminResponse.model_fields})


def _connection_response(target, connection, host):
    address = f"[{host}]" if ":" in host else host
    rdp_file = (
        f"full address:s:{address}:{connection.port}\r\n"
        f"username:s:{connection.username}\r\n"
        "prompt for credentials:i:1\r\n"
        "enablecredsspsupport:i:1\r\n"
        "redirectclipboard:i:0\r\n"
        "redirectprinters:i:0\r\n"
        "drivestoredirect:s:\r\n"
    )
    return WindowsVmConnectionResponse(
        resource_target_id=target.resource_target_id,
        name=target.instance_name,
        host=host,
        port=connection.port,
        username=connection.username,
        rdp_file=rdp_file,
    )


@router.get("", response_model=WindowsVmAvailableListResponse, responses=errors)
def list_available_windows_vms(
    response: Response,
    after: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
    session: Session = Depends(get_db_session),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        items, next_cursor = WindowsRuntimeService(session).available(after=after, limit=limit)
    except SQLAlchemyError as error:
        return _error(session, error)
    return WindowsVmAvailableListResponse(
        items=[_connection_response(*item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/{resource_target_id}/connection", response_model=WindowsVmConnectionResponse, responses=errors)
def get_windows_vm_connection(resource_target_id: UUID, response: Response, session: Session = Depends(get_db_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        target, connection, host = WindowsRuntimeService(session).connection(resource_target_id)
    except (WindowsRuntimeError, SQLAlchemyError) as error:
        return _error(session, error)
    return _connection_response(target, connection, host)


@router.post("/{resource_target_id}/execution-events", response_model=WindowsVmExecutionResponse, responses=errors)
def record_windows_vm_execution(resource_target_id: UUID, request: WindowsVmExecutionEvent, response: Response, session: Session = Depends(get_db_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        outcome = WindowsRuntimeService(session).event(resource_target_id, **request.model_dump())
    except (WindowsRuntimeError, SQLAlchemyError) as error:
        return _error(session, error)
    execution = outcome.execution
    return WindowsVmExecutionResponse(resource_target_id=resource_target_id, execution_id=execution.execution_id, execution_state="FINISHED" if execution.finished_at else "RUNNING", vm_state=outcome.connection.state, started_at=execution.started_at, finished_at=execution.finished_at, result=execution.result, exit_code=execution.exit_code, replayed=outcome.replayed)


@admin_router.get("/{resource_target_id}/connection", response_model=WindowsVmConnectionAdminResponse, responses=errors)
def get_admin_windows_vm_connection(resource_target_id: UUID, response: Response, session: Session = Depends(get_db_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return _admin_response(WindowsRuntimeService(session).configured_connection(resource_target_id))
    except (WindowsRuntimeError, SQLAlchemyError) as error:
        return _error(session, error)


@admin_router.put("/{resource_target_id}/connection", response_model=WindowsVmConnectionAdminResponse, responses=errors)
def configure_windows_vm_connection(resource_target_id: UUID, request: WindowsVmConnectionUpdate, response: Response, session: Session = Depends(get_db_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return _admin_response(WindowsRuntimeService(session).configure(resource_target_id, **request.model_dump()))
    except (WindowsRuntimeError, SQLAlchemyError) as error:
        return _error(session, error)
