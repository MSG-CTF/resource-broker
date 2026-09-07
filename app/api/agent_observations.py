from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.repositories.runtime_observations import RuntimeContainerSnapshot
from app.schemas.agent_observation import (
    AgentCapacity,
    AgentNodeUsage,
    AgentObservationRequest,
    AgentObservationResponse,
)
from app.schemas.provider_account import ErrorResponse
from app.services.agent_observation_service import (
    AgentObservationService,
    FutureAgentObservationError,
    ObservationCapacity,
    ObservationUsage,
    ResourceTargetNotFoundError,
    RetiredResourceTargetError,
    StaleAgentObservationError,
)


router = APIRouter(prefix="/v1/agent", tags=["agent"])


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


def _capacity(capacity: AgentCapacity) -> ObservationCapacity:
    return ObservationCapacity(
        cpu_millicores=capacity.cpu_millicores,
        memory_mib=capacity.memory_mib,
        ephemeral_storage_mib=capacity.ephemeral_storage_mib,
    )


def _usage(usage: AgentNodeUsage | None) -> ObservationUsage | None:
    if usage is None:
        return None
    return ObservationUsage(
        cpu_millicores=usage.cpu_millicores,
        memory_mib=usage.memory_mib,
    )


@router.post(
    "/observations",
    response_model=AgentObservationResponse,
    summary="Record a VM runtime observation",
    description=(
        "A node agent running inside a VM sends Kubernetes node capacity, "
        "whole-node CPU and memory usage, allocated requests, and the current "
        "container snapshot to the Broker."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def record_agent_observation(
    request: AgentObservationRequest,
    cert_verify: Annotated[
        str | None,
        Header(alias="X-Agent-Cert-Verify"),
    ] = None,
    cert_fingerprint: Annotated[
        str | None,
        Header(alias="X-Agent-Cert-Fingerprint"),
    ] = None,
    cert_resource_target_id: Annotated[
        str | None,
        Header(alias="X-Agent-Resource-Target-ID"),
    ] = None,
    session: Session = Depends(get_db_session),
) -> AgentObservationResponse | Response:
    if (
        cert_verify != "SUCCESS"
        or cert_fingerprint is None
        or not cert_fingerprint.strip()
        or cert_resource_target_id is None
    ):
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            "AGENT_CERTIFICATE_REQUIRED",
            "A verified node agent client certificate is required.",
        )

    try:
        authenticated_resource_target_id = UUID(
            cert_resource_target_id.strip()
        )
    except (AttributeError, ValueError):
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_AGENT_CERTIFICATE_IDENTITY",
            "The node agent certificate identity is invalid.",
        )

    if authenticated_resource_target_id != request.resource_target_id:
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            "AGENT_RESOURCE_TARGET_MISMATCH",
            "The node agent cannot report observations for this VM.",
        )

    containers = tuple(
        RuntimeContainerSnapshot(
            container_id=item.container_id,
            container_name=item.container_name,
            pod_name=item.pod_name,
            namespace=item.namespace,
            status=item.status,
            cpu_request_millicores=item.cpu_request_millicores,
            memory_request_mib=item.memory_request_mib,
            ephemeral_storage_request_mib=(
                item.ephemeral_storage_request_mib
            ),
            cpu_usage_millicores=item.cpu_usage_millicores,
            memory_usage_mib=item.memory_usage_mib,
            storage_usage_mib=item.storage_usage_mib,
        )
        for item in request.containers
    )

    try:
        outcome = AgentObservationService(session).record(
            resource_target_id=request.resource_target_id,
            observed_at=request.observed_at,
            snapshot_complete=request.snapshot_complete,
            runtime_type=request.runtime.type,
            runtime_target_id=request.runtime.target_id,
            runtime_ready=request.runtime.ready,
            node_allocatable=_capacity(request.node_allocatable),
            allocated_requests=_capacity(request.allocated_requests),
            containers=containers,
            node_usage=_usage(request.node_usage),
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
            "A retired resource target cannot accept observations.",
        )
    except StaleAgentObservationError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "STALE_AGENT_OBSERVATION",
            "The observation is older than the current runtime snapshot.",
        )
    except FutureAgentObservationError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "FUTURE_AGENT_OBSERVATION",
            "The observation is more than 30 seconds ahead of the Broker clock.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The node agent observation could not be saved.",
        )

    return AgentObservationResponse(
        resource_target_id=outcome.resource_target_id,
        observed_at=outcome.observed_at,
        accepted_at=outcome.accepted_at,
        snapshot_complete=outcome.snapshot_complete,
        containers_received=outcome.containers_received,
        containers_created=outcome.containers_created,
        containers_updated=outcome.containers_updated,
        containers_deleted=outcome.containers_deleted,
        node_usage=(
            AgentNodeUsage(
                cpu_millicores=outcome.node_usage.cpu_millicores,
                memory_mib=outcome.node_usage.memory_mib,
            )
            if outcome.node_usage is not None
            else None
        ),
        allocatable_capacity=AgentCapacity(
            cpu_millicores=outcome.allocatable_capacity.cpu_millicores,
            memory_mib=outcome.allocatable_capacity.memory_mib,
            ephemeral_storage_mib=(
                outcome.allocatable_capacity.ephemeral_storage_mib
            ),
        ),
    )
