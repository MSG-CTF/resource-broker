from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.schemas.agent_enrollment import (
    AgentEnrollmentTokenCreateRequest,
    AgentEnrollmentTokenResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.agent_enrollment_service import (
    AgentEnrollmentService,
    ResourceTargetNotFoundError,
    RetiredResourceTargetError,
)


router = APIRouter(
    prefix="/v1/admin/resource-targets",
    tags=["admin-agent-enrollment"],
    dependencies=[Depends(require_admin)],
)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/{resource_target_id}/enrollment-tokens",
    response_model=AgentEnrollmentTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a one-time Node Agent enrollment token",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_agent_enrollment_token(
    resource_target_id: UUID,
    request: AgentEnrollmentTokenCreateRequest,
    session: Session = Depends(get_db_session),
) -> AgentEnrollmentTokenResponse | Response:
    try:
        outcome = AgentEnrollmentService(session).issue_token(
            resource_target_id=resource_target_id,
            expires_in_seconds=request.expires_in_seconds,
        )
    except ResourceTargetNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "RESOURCE_TARGET_NOT_FOUND",
            "The resource target was not found.",
        )
    except RetiredResourceTargetError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "RESOURCE_TARGET_RETIRED",
            "A retired resource target cannot be enrolled.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The Agent enrollment token could not be created.",
        )

    return AgentEnrollmentTokenResponse(
        enrollment_id=outcome.enrollment_id,
        resource_target_id=outcome.resource_target_id,
        token=outcome.token,
        expires_at=outcome.expires_at,
        created_at=outcome.created_at,
    )
