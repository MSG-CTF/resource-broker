from fastapi import APIRouter, Depends, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.agent_enrollment import (
    AgentEnrollmentRequest,
    AgentEnrollmentResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.agent_enrollment_service import (
    AgentCertificateSigningError,
    AgentEnrollmentConfigurationError,
    AgentEnrollmentService,
    EnrollmentResourceTargetMismatchError,
    InvalidCertificateSigningRequestError,
    InvalidEnrollmentTokenError,
    ResourceTargetNotFoundError,
    RetiredResourceTargetError,
)


router = APIRouter(prefix="/v1/agent", tags=["agent-enrollment"])
enrollment_bearer = HTTPBearer(auto_error=False)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    authenticate: bool = False,
) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"} if authenticate else None,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/enrollments",
    response_model=AgentEnrollmentResponse,
    summary="Exchange a one-time enrollment token and CSR for an Agent certificate",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "The enrollment gateway rate limit was exceeded."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def enroll_agent(
    request: AgentEnrollmentRequest,
    credentials: HTTPAuthorizationCredentials | None = Security(
        enrollment_bearer
    ),
    session: Session = Depends(get_db_session),
) -> AgentEnrollmentResponse | Response:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            "ENROLLMENT_TOKEN_REQUIRED",
            "A valid one-time enrollment Bearer token is required.",
            authenticate=True,
        )

    try:
        outcome = AgentEnrollmentService(session).enroll(
            token=credentials.credentials,
            resource_target_id=request.resource_target_id,
            csr_pem=request.certificate_signing_request_pem,
        )
    except InvalidEnrollmentTokenError:
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_ENROLLMENT_TOKEN",
            "The enrollment token is invalid, expired, or already used.",
            authenticate=True,
        )
    except EnrollmentResourceTargetMismatchError:
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            "ENROLLMENT_RESOURCE_TARGET_MISMATCH",
            "The enrollment token cannot enroll this resource target.",
        )
    except InvalidCertificateSigningRequestError:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_AGENT_CSR",
            "The Agent certificate signing request is invalid.",
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
    except AgentEnrollmentConfigurationError:
        session.rollback()
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AGENT_ENROLLMENT_NOT_CONFIGURED",
            "Agent certificate enrollment is not configured.",
        )
    except AgentCertificateSigningError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "AGENT_CERTIFICATE_ISSUANCE_FAILED",
            "The Agent certificate could not be issued.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The Agent enrollment could not be saved.",
        )

    return AgentEnrollmentResponse(
        resource_target_id=outcome.resource_target_id,
        client_certificate_pem=outcome.client_certificate_pem,
        client_ca_pem=outcome.client_ca_pem,
        serial_number=outcome.serial_number,
        fingerprint_sha256=outcome.fingerprint_sha256,
        not_before=outcome.not_before,
        not_after=outcome.not_after,
        issued_at=outcome.issued_at,
    )
