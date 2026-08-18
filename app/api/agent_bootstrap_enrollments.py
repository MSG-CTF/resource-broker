from uuid import UUID

from fastapi import APIRouter, Depends, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.agent_enrollment import AgentEnrollmentRequest, AgentEnrollmentResponse
from app.schemas.provider_account import ErrorResponse
from app.services.agent_enrollment_service import (
    AgentCertificateSigningError,
    AgentEnrollmentConfigurationError,
    InvalidCertificateSigningRequestError,
    ResourceTargetNotFoundError,
    RetiredResourceTargetError,
)
from app.services.cloud_bootstrap_enrollment_service import (
    BootstrapEnrollmentUnavailableError,
    CloudBootstrapEnrollmentService,
    CloudIdentityTargetMismatchError,
    InvalidCloudIdentityError,
)


router = APIRouter(prefix="/v1/agent", tags=["agent-bootstrap-enrollment"])
bearer = HTTPBearer(auto_error=False)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/bootstrap-enrollments/{job_id}",
    response_model=AgentEnrollmentResponse,
    summary="Exchange a cloud VM identity and CSR for an Agent certificate",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def enroll_bootstrap_agent(
    job_id: UUID,
    request: AgentEnrollmentRequest,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    session: Session = Depends(get_db_session),
) -> AgentEnrollmentResponse | Response:
    token = None
    if credentials is not None:
        if credentials.scheme.lower() != "bearer":
            return _error(
                401,
                "CLOUD_IDENTITY_REQUIRED",
                "A supported cloud VM identity is required.",
            )
        token = credentials.credentials
    try:
        outcome = CloudBootstrapEnrollmentService(session).enroll(
            job_id=job_id,
            token=token,
            resource_target_id=request.resource_target_id,
            csr_pem=request.certificate_signing_request_pem,
            aws_instance_identity_document=(
                request.aws_instance_identity_document
            ),
            aws_instance_identity_signature=(
                request.aws_instance_identity_signature
            ),
            azure_attested_document=request.azure_attested_document,
        )
    except InvalidCloudIdentityError:
        return _error(
            401,
            "INVALID_CLOUD_IDENTITY",
            "The cloud VM identity proof is invalid.",
        )
    except CloudIdentityTargetMismatchError:
        return _error(
            403,
            "CLOUD_IDENTITY_TARGET_MISMATCH",
            "The cloud VM identity does not match this resource target.",
        )
    except BootstrapEnrollmentUnavailableError:
        return _error(409, "BOOTSTRAP_ENROLLMENT_UNAVAILABLE", "This bootstrap enrollment is unavailable or already used.")
    except InvalidCertificateSigningRequestError:
        return _error(400, "INVALID_AGENT_CSR", "The Agent certificate signing request is invalid.")
    except ResourceTargetNotFoundError:
        return _error(404, "RESOURCE_TARGET_NOT_FOUND", "The resource target was not found.")
    except RetiredResourceTargetError:
        return _error(409, "RESOURCE_TARGET_RETIRED", "A retired resource target cannot be enrolled.")
    except AgentEnrollmentConfigurationError:
        session.rollback()
        return _error(503, "AGENT_ENROLLMENT_NOT_CONFIGURED", "Agent certificate enrollment is not configured.")
    except AgentCertificateSigningError:
        session.rollback()
        return _error(500, "AGENT_CERTIFICATE_ISSUANCE_FAILED", "The Agent certificate could not be issued.")
    except SQLAlchemyError:
        session.rollback()
        return _error(500, "DATABASE_ERROR", "The Agent enrollment could not be saved.")
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
