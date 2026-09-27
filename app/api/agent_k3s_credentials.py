from uuid import UUID

from fastapi import APIRouter, Depends, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.k3s_credential import (
    K3sCredentialUploadRequest,
    K3sCredentialUploadResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.k3s_credential_service import (
    InvalidK3sCredentialError,
    InvalidK3sCredentialUploadTokenError,
    K3sCredentialConfigurationError,
    K3sCredentialService,
    K3sCredentialUploadTargetMismatchError,
    K3sCredentialUploadUnavailableError,
)


router = APIRouter(prefix="/v1/agent", tags=["agent-k3s-credentials"])
bearer = HTTPBearer(auto_error=False)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/bootstrap-jobs/{job_id}/k3s-credentials",
    response_model=K3sCredentialUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def upload_k3s_credential(
    job_id: UUID,
    request: K3sCredentialUploadRequest,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    session: Session = Depends(get_db_session),
) -> K3sCredentialUploadResponse | Response:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return _error(
            401,
            "K3S_CREDENTIAL_UPLOAD_TOKEN_REQUIRED",
            "A valid Bootstrap credential upload token is required.",
        )
    try:
        stored = K3sCredentialService(session).store_from_bootstrap(
            path_job_id=job_id,
            token=credentials.credentials,
            bootstrap_job_id=request.bootstrap_job_id,
            resource_target_id=request.resource_target_id,
            server_url=request.server_url,
            kubeconfig_base64=request.kubeconfig_base64,
            client_certificate_fingerprint_sha256=(
                request.client_certificate_fingerprint_sha256
            ),
            client_certificate_not_after=(
                request.client_certificate_not_after
            ),
            generated_at=request.generated_at,
        )
    except InvalidK3sCredentialUploadTokenError:
        return _error(
            401,
            "INVALID_K3S_CREDENTIAL_UPLOAD_TOKEN",
            "The Bootstrap credential upload token is invalid or expired.",
        )
    except K3sCredentialUploadTargetMismatchError:
        return _error(
            403,
            "K3S_CREDENTIAL_UPLOAD_TARGET_MISMATCH",
            "The credential does not match this Bootstrap job.",
        )
    except K3sCredentialUploadUnavailableError:
        return _error(
            409,
            "K3S_CREDENTIAL_UPLOAD_UNAVAILABLE",
            "This Bootstrap job cannot accept a credential upload.",
        )
    except InvalidK3sCredentialError:
        return _error(
            422,
            "INVALID_K3S_CREDENTIAL",
            "The submitted k3s administrator credential is invalid.",
        )
    except K3sCredentialConfigurationError:
        session.rollback()
        return _error(
            503,
            "K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED",
            "k3s credential storage is not configured.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The k3s credential could not be stored.",
        )
    return K3sCredentialUploadResponse(
        resource_target_id=stored.resource_target_id,
        source_bootstrap_job_id=stored.source_job_id,
        uploaded_at=stored.uploaded_at,
    )
