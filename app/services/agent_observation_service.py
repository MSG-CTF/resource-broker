from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.enums import RuntimeType
from app.repositories.runtime_observations import (
    RuntimeContainerSnapshot,
    RuntimeObservationRepository,
)


class ResourceTargetNotFoundError(LookupError):
    pass


class RetiredResourceTargetError(ValueError):
    pass


class StaleAgentObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ObservationCapacity:
    cpu_millicores: int
    memory_mib: int
    ephemeral_storage_mib: int


@dataclass(frozen=True, slots=True)
class AgentObservationOutcome:
    resource_target_id: UUID
    observed_at: datetime
    accepted_at: datetime
    snapshot_complete: bool
    containers_received: int
    containers_created: int
    containers_updated: int
    containers_deleted: int
    allocatable_capacity: ObservationCapacity


class AgentObservationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._observations = RuntimeObservationRepository(session)

    def record(
        self,
        *,
        resource_target_id: UUID,
        observed_at: datetime,
        snapshot_complete: bool,
        runtime_type: RuntimeType,
        runtime_target_id: str,
        runtime_ready: bool,
        node_allocatable: ObservationCapacity,
        allocated_requests: ObservationCapacity,
        containers: tuple[RuntimeContainerSnapshot, ...],
    ) -> AgentObservationOutcome:
        resource = self._observations.get_resource_target_for_update(
            resource_target_id
        )
        if resource is None:
            raise ResourceTargetNotFoundError
        if resource.retired_at is not None:
            raise RetiredResourceTargetError
        current_runtime_observed_at = resource.runtime_observed_at
        if (
            current_runtime_observed_at is not None
            and current_runtime_observed_at.tzinfo is None
        ):
            current_runtime_observed_at = current_runtime_observed_at.replace(
                tzinfo=UTC
            )
        if (
            current_runtime_observed_at is not None
            and observed_at < current_runtime_observed_at
        ):
            raise StaleAgentObservationError

        allocatable = ObservationCapacity(
            cpu_millicores=max(
                node_allocatable.cpu_millicores
                - allocated_requests.cpu_millicores,
                0,
            ),
            memory_mib=max(
                node_allocatable.memory_mib
                - allocated_requests.memory_mib,
                0,
            ),
            ephemeral_storage_mib=max(
                node_allocatable.ephemeral_storage_mib
                - allocated_requests.ephemeral_storage_mib,
                0,
            ),
        )
        accepted_at = datetime.now(UTC)
        result = self._observations.apply_snapshot(
            resource=resource,
            observed_at=observed_at,
            accepted_at=accepted_at,
            snapshot_complete=snapshot_complete,
            runtime_type=runtime_type,
            runtime_target_id=runtime_target_id,
            runtime_ready=runtime_ready,
            allocatable_cpu_millicores=allocatable.cpu_millicores,
            allocatable_memory_mib=allocatable.memory_mib,
            allocatable_ephemeral_storage_mib=(
                allocatable.ephemeral_storage_mib
            ),
            containers=containers,
        )
        self._session.commit()

        return AgentObservationOutcome(
            resource_target_id=resource_target_id,
            observed_at=observed_at,
            accepted_at=accepted_at,
            snapshot_complete=snapshot_complete,
            containers_received=len(containers),
            containers_created=result.created_count,
            containers_updated=result.updated_count,
            containers_deleted=result.deleted_count,
            allocatable_capacity=allocatable,
        )
