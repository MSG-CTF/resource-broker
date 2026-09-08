from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.scheduler_auth import require_scheduler
from app.db.session import get_db_session
from app.db.tables import ReservationTable
from app.schemas.candidate_request import ResourceProfile
from app.schemas.provider_account import ErrorResponse
from app.schemas.reservation import (
    ReservationCapacity,
    ReservationCommitRequest,
    ReservationCreateRequest,
    ReservationReleaseRequest,
    ReservationResponse,
)
from app.services.reservation_service import (
    ActiveInstanceReservationError,
    CandidateNotFoundError,
    CandidateUnavailableError,
    DeployedSpecMismatchError,
    InsufficientCandidateCapacityError,
    ReservationConflictError,
    ReservationIdempotencyConflictError,
    ReservationInstanceMismatchError,
    ReservationMutationConflictError,
    ReservationNotFoundError,
    ReservationService,
    ReservationStateConflictError,
)
from app.services.scheduler_settings import SchedulerConfigurationError


router = APIRouter(
    prefix="/v1/reservations",
    tags=["scheduler-reservations"],
    dependencies=[Depends(require_scheduler)],
)


def _response(reservation: ReservationTable) -> ReservationResponse:
    target_id = reservation.resource_target.target_id
    if target_id is None:
        raise CandidateUnavailableError
    deployed_profile = None
    if (
        reservation.deployed_cpu_millicores is not None
        and reservation.deployed_memory_mib is not None
        and reservation.deployed_ephemeral_storage_mib is not None
    ):
        deployed_profile = ResourceProfile(
            cpu_millicores=reservation.deployed_cpu_millicores,
            memory_mib=reservation.deployed_memory_mib,
            ephemeral_storage_mib=(
                reservation.deployed_ephemeral_storage_mib
            ),
        )

    return ReservationResponse(
        reservation_id=reservation.reservation_id,
        request_id=reservation.request_id,
        candidate_id=reservation.resource_target_id,
        target_id=target_id,
        team_id=reservation.team_id,
        challenge_id=reservation.challenge_id,
        instance_id=reservation.instance_id,
        resource_profile=ReservationCapacity(
            cpu_millicores=reservation.cpu_millicores,
            memory_mib=reservation.memory_mib,
            ephemeral_storage_mib=reservation.ephemeral_storage_mib,
            architecture=reservation.architecture,
        ),
        status=reservation.status,
        expires_at=reservation.expires_at,
        created_at=reservation.created_at,
        committed_at=reservation.committed_at,
        released_at=reservation.released_at,
        runtime_workload_id=reservation.runtime_workload_id,
        deployed_resource_profile=deployed_profile,
        release_reason=reservation.release_reason,
        updated_at=reservation.updated_at,
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Hold capacity on a candidate",
    responses={
        status.HTTP_200_OK: {
            "model": ReservationResponse,
            "description": "Identical idempotent replay.",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_reservation(
    request: ReservationCreateRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> ReservationResponse | Response:
    profile = request.resource_profile
    try:
        outcome = ReservationService(session).create(
            request_id=request.request_id,
            requested_at=request.requested_at,
            candidate_id=request.candidate_id,
            team_id=request.team_id,
            challenge_id=request.challenge_id,
            instance_id=request.instance_id,
            cpu_millicores=profile.cpu_millicores,
            memory_mib=profile.memory_mib,
            ephemeral_storage_mib=profile.ephemeral_storage_mib,
            architecture=request.architecture,
        )
    except CandidateNotFoundError:
        return _error(
            404,
            "CANDIDATE_NOT_FOUND",
            "The candidate was not found.",
        )
    except CandidateUnavailableError:
        return _error(
            409,
            "CANDIDATE_UNAVAILABLE",
            "The candidate is no longer eligible.",
        )
    except InsufficientCandidateCapacityError:
        return _error(
            409,
            "INSUFFICIENT_CAPACITY",
            "The candidate no longer has enough capacity.",
        )
    except ReservationIdempotencyConflictError:
        return _error(
            409,
            "REQUEST_ID_REUSED",
            "The request ID was already used for another reservation.",
        )
    except ActiveInstanceReservationError:
        return _error(
            409,
            "INSTANCE_ALREADY_RESERVED",
            "The instance already has an active reservation.",
        )
    except ReservationConflictError:
        return _error(
            409,
            "RESERVATION_CONFLICT",
            "The reservation conflicts with another request.",
        )
    except SchedulerConfigurationError:
        return _error(
            503,
            "SCHEDULER_NOT_CONFIGURED",
            "Scheduler capacity settings are not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The reservation could not be created.",
        )

    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return _response(outcome.reservation)


@router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="Get a reservation",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def get_reservation(
    reservation_id: UUID,
    session: Session = Depends(get_db_session),
) -> ReservationResponse | Response:
    try:
        reservation = ReservationService(session).get(reservation_id)
        return _response(reservation)
    except ReservationNotFoundError:
        return _error(
            404,
            "RESERVATION_NOT_FOUND",
            "The reservation was not found.",
        )
    except SchedulerConfigurationError:
        return _error(
            503,
            "SCHEDULER_NOT_CONFIGURED",
            "Scheduler capacity settings are not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The reservation could not be loaded.",
        )


@router.post(
    "/{reservation_id}/commit",
    response_model=ReservationResponse,
    summary="Commit a reservation after Runtime creation succeeds",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def commit_reservation(
    reservation_id: UUID,
    request: ReservationCommitRequest,
    session: Session = Depends(get_db_session),
) -> ReservationResponse | Response:
    if request.reservation_id != reservation_id:
        return _error(
            409,
            "RESERVATION_ID_MISMATCH",
            "The path and body reservation IDs do not match.",
        )
    profile = request.resource_profile
    try:
        reservation = ReservationService(session).commit(
            reservation_id,
            request_id=request.request_id,
            requested_at=request.requested_at,
            instance_id=request.instance_id,
            runtime_workload_id=request.runtime_workload_id,
            cpu_millicores=profile.cpu_millicores,
            memory_mib=profile.memory_mib,
            ephemeral_storage_mib=profile.ephemeral_storage_mib,
        )
        return _response(reservation)
    except ReservationNotFoundError:
        return _error(
            404,
            "RESERVATION_NOT_FOUND",
            "The reservation was not found.",
        )
    except ReservationStateConflictError:
        return _error(
            409,
            "INVALID_RESERVATION_STATE",
            "The reservation cannot be committed in its current state.",
        )
    except ReservationInstanceMismatchError:
        return _error(
            409,
            "INSTANCE_ID_MISMATCH",
            "The instance does not own this reservation.",
        )
    except DeployedSpecMismatchError:
        return _error(
            409,
            "DEPLOYED_SPEC_MISMATCH",
            "The deployed resource profile differs from the reservation.",
        )
    except ReservationMutationConflictError:
        return _error(
            409,
            "REQUEST_ID_REUSED",
            "The commit request ID was already used with different data.",
        )
    except SchedulerConfigurationError:
        return _error(
            503,
            "SCHEDULER_NOT_CONFIGURED",
            "Scheduler capacity settings are not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The reservation could not be committed.",
        )


@router.post(
    "/{reservation_id}/release",
    response_model=ReservationResponse,
    summary="Release capacity after failure or workload deletion",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def release_reservation(
    reservation_id: UUID,
    request: ReservationReleaseRequest,
    session: Session = Depends(get_db_session),
) -> ReservationResponse | Response:
    if request.reservation_id != reservation_id:
        return _error(
            409,
            "RESERVATION_ID_MISMATCH",
            "The path and body reservation IDs do not match.",
        )
    try:
        reservation = ReservationService(session).release(
            reservation_id,
            request_id=request.request_id,
            requested_at=request.requested_at,
            instance_id=request.instance_id,
            release_reason=request.release_reason,
        )
        return _response(reservation)
    except ReservationNotFoundError:
        return _error(
            404,
            "RESERVATION_NOT_FOUND",
            "The reservation was not found.",
        )
    except ReservationStateConflictError:
        return _error(
            409,
            "INVALID_RESERVATION_STATE",
            "The reservation cannot be released in its current state.",
        )
    except ReservationInstanceMismatchError:
        return _error(
            409,
            "INSTANCE_ID_MISMATCH",
            "The instance does not own this reservation.",
        )
    except ReservationMutationConflictError:
        return _error(
            409,
            "REQUEST_ID_REUSED",
            "The release request ID was already used with different data.",
        )
    except SchedulerConfigurationError:
        return _error(
            503,
            "SCHEDULER_NOT_CONFIGURED",
            "Scheduler capacity settings are not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The reservation could not be released.",
        )
