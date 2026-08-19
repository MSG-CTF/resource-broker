from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.scheduler_auth import require_scheduler
from app.db.session import get_db_session
from app.schemas.candidate_request import CandidateQueryRequest
from app.schemas.candidate_response import (
    Candidate,
    CandidateQueryResponse,
    RemainingCapacity,
    RuntimeTarget,
)
from app.schemas.provider_account import ErrorResponse
from app.services.candidate_service import CandidateService
from app.services.scheduler_settings import SchedulerConfigurationError


router = APIRouter(
    prefix="/v1/candidates",
    tags=["scheduler-candidates"],
    dependencies=[Depends(require_scheduler)],
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/query",
    response_model=CandidateQueryResponse,
    summary="Find runtime-ready candidates for a ResourceProfile",
    description=(
        "Returns best-fit candidates from the Broker DB snapshot after "
        "subtracting reservations that are not yet represented by a newer "
        "Node Agent observation. Reservation creation revalidates capacity."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def query_candidates(
    request: CandidateQueryRequest,
    session: Session = Depends(get_db_session),
) -> CandidateQueryResponse | Response:
    profile = request.resource_profile
    try:
        outcome = CandidateService(session).query(
            cpu_millicores=profile.cpu_millicores,
            memory_mib=profile.memory_mib,
            ephemeral_storage_mib=profile.ephemeral_storage_mib,
            architecture=profile.architecture,
            max_candidates=request.max_candidates,
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
            "Candidates could not be loaded.",
        )

    candidates: list[Candidate] = []
    for match in outcome.candidates:
        resource = match.resource
        if (
            resource.runtime_type is None
            or resource.target_id is None
            or resource.architecture is None
            or resource.runtime_observed_at is None
        ):
            continue
        candidates.append(
            Candidate(
                candidate_id=resource.resource_target_id,
                provider=resource.account.provider,
                account_id=resource.account_id,
                region=resource.region,
                zone=resource.zone,
                runtime=RuntimeTarget(
                    type=resource.runtime_type,
                    target_id=resource.target_id,
                ),
                architecture=resource.architecture,
                remaining_capacity=RemainingCapacity(
                    cpu_millicores=match.remaining_cpu_millicores,
                    memory_mib=match.remaining_memory_mib,
                    ephemeral_storage_mib=(
                        match.remaining_ephemeral_storage_mib
                    ),
                    fit_count=match.fit_count,
                ),
                runtime_observed_at=resource.runtime_observed_at,
                valid_until=match.valid_until,
            )
        )

    return CandidateQueryResponse(
        request_id=request.request_id,
        generated_at=outcome.generated_at,
        status=outcome.status,
        candidates=candidates,
    )
