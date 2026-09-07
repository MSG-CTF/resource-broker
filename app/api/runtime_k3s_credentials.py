from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.runtime_auth import require_runtime
from app.db.session import get_db_session
from app.schemas.k3s_credential import (
    RuntimeK3sCredentialListResponse,
    RuntimeK3sCredentialResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.k3s_credential_service import (
    K3sCredentialConfigurationError,
    K3sCredentialDecryptionError,
    K3sCredentialNotFoundError,
    K3sCredentialService,
)


router = APIRouter(
    prefix="/v1/runtime",
    tags=["runtime-k3s-credentials"],
    dependencies=[Depends(require_runtime)],
)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


def _response(credential) -> RuntimeK3sCredentialResponse:
    return RuntimeK3sCredentialResponse(
        resource_target_id=credential.resource_target_id,
        source_bootstrap_job_id=credential.source_job_id,
        server_url=credential.server_url,
        kubeconfig_base64=credential.kubeconfig_base64,
        client_certificate_fingerprint_sha256=(
            credential.client_certificate_fingerprint_sha256
        ),
        client_certificate_not_after=(
            credential.client_certificate_not_after
        ),
        generated_at=credential.generated_at,
        uploaded_at=credential.uploaded_at,
    )


@router.get(
    "/k3s-credentials",
    response_model=RuntimeK3sCredentialListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def list_k3s_credentials(
    after: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> RuntimeK3sCredentialListResponse | Response:
    try:
        credentials, next_cursor = K3sCredentialService(
            session
        ).list_for_runtime(after=after, limit=limit)
    except K3sCredentialConfigurationError:
        return _error(
            503,
            "K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED",
            "k3s credential storage is not configured.",
        )
    except K3sCredentialDecryptionError:
        return _error(
            500,
            "K3S_CREDENTIAL_DECRYPTION_FAILED",
            "A stored k3s credential could not be decrypted.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The k3s credentials could not be loaded.",
        )
    return RuntimeK3sCredentialListResponse(
        items=[_response(credential) for credential in credentials],
        next_cursor=next_cursor,
    )


@router.get(
    "/k3s-credentials/{resource_target_id}",
    response_model=RuntimeK3sCredentialResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def get_k3s_credential(
    resource_target_id: UUID,
    session: Session = Depends(get_db_session),
) -> RuntimeK3sCredentialResponse | Response:
    try:
        credential = K3sCredentialService(session).get_for_runtime(
            resource_target_id
        )
    except K3sCredentialNotFoundError:
        return _error(
            404,
            "K3S_CREDENTIAL_NOT_FOUND",
            "The k3s credential was not found.",
        )
    except K3sCredentialConfigurationError:
        return _error(
            503,
            "K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED",
            "k3s credential storage is not configured.",
        )
    except K3sCredentialDecryptionError:
        return _error(
            500,
            "K3S_CREDENTIAL_DECRYPTION_FAILED",
            "The stored k3s credential could not be decrypted.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error(
            500,
            "DATABASE_ERROR",
            "The k3s credential could not be loaded.",
        )
    return _response(credential)
