from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.scheduler_auth import require_scheduler
from app.db.session import get_db_session
from app.db.tables import WindowsWorkerLeaseTable
from app.schemas.provider_account import ErrorResponse
from app.schemas.windows_worker import (
    LeasedWindowsWorkerResponse,
    WindowsWorkerLeaseCompletionResponse,
    WindowsWorkerLeaseResponse,
)
from app.services.windows_worker_service import (
    NoWindowsWorkerAvailableError,
    WindowsWorkerConfigurationError,
    WindowsWorkerLeaseNotFoundError,
    WindowsWorkerService,
)


router = APIRouter(
    prefix="/v1/windows-worker-leases",
    tags=["scheduler-windows-workers"],
    dependencies=[Depends(require_scheduler)],
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


def _response(lease: WindowsWorkerLeaseTable) -> WindowsWorkerLeaseResponse:
    address = lease.worker.private_ip
    if address is None:
        raise NoWindowsWorkerAvailableError
    return WindowsWorkerLeaseResponse(
        lease_id=lease.lease_id,
        status=lease.status,
        expires_at=lease.expires_at,
        worker=LeasedWindowsWorkerResponse(
            worker_id=lease.worker_id,
            address=address,
        ),
    )


@router.post(
    "",
    response_model=WindowsWorkerLeaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Lease one ready Windows worker",
    responses={
        status.HTTP_200_OK: {
            "model": WindowsWorkerLeaseResponse,
            "description": "Idempotent replay.",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_windows_worker_lease(
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    session: Session = Depends(get_db_session),
) -> WindowsWorkerLeaseResponse | Response:
    try:
        parsed_key = UUID(idempotency_key)
    except ValueError:
        return _error(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must be a UUID.",
        )

    try:
        outcome = WindowsWorkerService(session).lease(parsed_key)
        lease_response = _response(outcome.lease)
    except NoWindowsWorkerAvailableError:
        return _error(
            409,
            "NO_WINDOWS_WORKER_AVAILABLE",
            "No healthy Windows worker is currently available.",
        )
    except WindowsWorkerConfigurationError:
        return _error(
            503,
            "WINDOWS_WORKER_NOT_CONFIGURED",
            "Windows worker lease settings are not configured correctly.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The Windows worker lease could not be created.",
        )

    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return lease_response


@router.post(
    "/{lease_id}/complete",
    response_model=WindowsWorkerLeaseCompletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Complete a lease and queue worker recreation",
    responses={
        status.HTTP_200_OK: {
            "model": WindowsWorkerLeaseCompletionResponse,
            "description": "Idempotent replay.",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def complete_windows_worker_lease(
    lease_id: UUID,
    response: Response,
    session: Session = Depends(get_db_session),
) -> WindowsWorkerLeaseCompletionResponse | Response:
    try:
        outcome = WindowsWorkerService(session).complete(lease_id)
    except WindowsWorkerLeaseNotFoundError:
        return _error(
            404,
            "WINDOWS_WORKER_LEASE_NOT_FOUND",
            "The Windows worker lease was not found.",
        )
    except WindowsWorkerConfigurationError:
        return _error(
            503,
            "WINDOWS_WORKER_NOT_CONFIGURED",
            "Windows worker lease settings are not configured correctly.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The Windows worker lease could not be completed.",
        )

    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return WindowsWorkerLeaseCompletionResponse(
        lease_id=outcome.lease.lease_id,
        status=outcome.lease.status,
    )
