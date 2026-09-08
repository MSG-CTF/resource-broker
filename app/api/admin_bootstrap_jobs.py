from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import BootstrapJobTable
from app.schemas.bootstrap_job import (
    BootstrapJobCreateRequest,
    BootstrapJobListResponse,
    BootstrapJobResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.bootstrap_artifacts import BootstrapArtifactNotFoundError
from app.services.bootstrap_job_service import (
    BootstrapJobConflictError,
    BootstrapJobService,
    BootstrapProviderNotImplementedError,
    BootstrapResourceTargetInvalidError,
    BootstrapResourceTargetNotFoundError,
)
from app.services.k3s_credential_service import (
    K3sCredentialConfigurationError,
    K3sCredentialTargetAddressError,
)


router = APIRouter(
    prefix="/v1/admin",
    tags=["admin-bootstrap"],
    dependencies=[Depends(require_admin)],
)


def _response(job: BootstrapJobTable) -> BootstrapJobResponse:
    return BootstrapJobResponse(
        job_id=job.job_id,
        resource_target_id=job.resource_target_id,
        provider=job.provider,
        action=job.action,
        status=job.status,
        bootstrap_version=job.bootstrap_version,
        k3s_version=job.k3s_version,
        agent_image=job.agent_image,
        provider_job_id=job.provider_job_id,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        deadline_at=job.deadline_at,
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


@router.post(
    "/resource-targets/{resource_target_id}/bootstrap-jobs",
    response_model=BootstrapJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_bootstrap_job(
    resource_target_id: UUID,
    request: BootstrapJobCreateRequest,
    session: Session = Depends(get_db_session),
) -> BootstrapJobResponse | Response:
    try:
        job = BootstrapJobService(session).create(
            resource_target_id=resource_target_id,
            action=request.action,
            bootstrap_version=request.bootstrap_version,
            k3s_version=request.k3s_version,
            agent_image=request.agent_image,
        )
    except BootstrapResourceTargetNotFoundError:
        return _error(404, "RESOURCE_TARGET_NOT_FOUND", "The resource target was not found.")
    except BootstrapResourceTargetInvalidError:
        return _error(422, "INVALID_RESOURCE_TARGET", "The resource target cannot run bootstrap jobs.")
    except BootstrapProviderNotImplementedError as error:
        return _error(
            422,
            "PROVIDER_BOOTSTRAP_NOT_IMPLEMENTED",
            f"Bootstrap automation is not implemented for {error.provider.value}.",
        )
    except BootstrapArtifactNotFoundError:
        return _error(422, "BOOTSTRAP_ARTIFACT_NOT_FOUND", "The requested bootstrap artifact is not available.")
    except K3sCredentialTargetAddressError:
        return _error(
            422,
            "K3S_ADDRESS_UNAVAILABLE",
            "The resource target does not have the configured k3s API IP address.",
        )
    except K3sCredentialConfigurationError:
        return _error(
            503,
            "K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED",
            "Broker k3s credential storage is not configured correctly.",
        )
    except BootstrapJobConflictError:
        return _error(409, "BOOTSTRAP_JOB_ALREADY_ACTIVE", "This resource target already has an active bootstrap job.")
    except SQLAlchemyError:
        session.rollback()
        return _error(500, "DATABASE_ERROR", "The bootstrap job could not be created.")
    return _response(job)


@router.get(
    "/resource-targets/{resource_target_id}/bootstrap-jobs",
    response_model=BootstrapJobListResponse,
)
def list_bootstrap_jobs(
    resource_target_id: UUID,
    session: Session = Depends(get_db_session),
) -> BootstrapJobListResponse | Response:
    try:
        jobs = BootstrapJobService(session).list_for_resource(resource_target_id)
    except BootstrapResourceTargetNotFoundError:
        return _error(404, "RESOURCE_TARGET_NOT_FOUND", "The resource target was not found.")
    except SQLAlchemyError:
        session.rollback()
        return _error(500, "DATABASE_ERROR", "The bootstrap jobs could not be loaded.")
    return BootstrapJobListResponse(items=[_response(job) for job in jobs])


@router.get(
    "/bootstrap-jobs/{job_id}",
    response_model=BootstrapJobResponse,
)
def get_bootstrap_job(
    job_id: UUID,
    session: Session = Depends(get_db_session),
) -> BootstrapJobResponse | Response:
    try:
        job = BootstrapJobService(session).get(job_id)
    except BootstrapResourceTargetNotFoundError:
        return _error(404, "BOOTSTRAP_JOB_NOT_FOUND", "The bootstrap job was not found.")
    except SQLAlchemyError:
        session.rollback()
        return _error(500, "DATABASE_ERROR", "The bootstrap job could not be loaded.")
    return _response(job)
