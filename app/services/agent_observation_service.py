from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.enums import RuntimeType
from app.domain.observation import OBSERVATION_CLOCK_SKEW
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


class FutureAgentObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ObservationCapacity:
    cpu_millicores: int
    memory_mib: int
    ephemeral_storage_mib: int


@dataclass(frozen=True, slots=True)
class ObservationUsage:
    cpu_millicores: int | None
    memory_mib: int | None


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
    node_usage: ObservationUsage | None
    allocatable_capacity: ObservationCapacity


class AgentObservationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._observations = RuntimeObservationRepository(session)

    @staticmethod
    def _effective_available(
        *,
        request_based_available: int,
        provider_capacity: int | None,
        actual_usage: int | None,
    ) -> int:
        if provider_capacity is None or actual_usage is None:
            return request_based_available
        actual_based_available = max(provider_capacity - actual_usage, 0)
        return min(request_based_available, actual_based_available)

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
        node_usage: ObservationUsage | None = None,
    ) -> AgentObservationOutcome:
        resource = self._observations.get_resource_target_for_update(
            resource_target_id
        )
        if resource is None:
            raise ResourceTargetNotFoundError
        if resource.retired_at is not None:
            raise RetiredResourceTargetError
        accepted_at = datetime.now(UTC)
        if observed_at > accepted_at + OBSERVATION_CLOCK_SKEW:
            raise FutureAgentObservationError
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

        request_based_available = ObservationCapacity(
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
        allocatable = ObservationCapacity(
            cpu_millicores=self._effective_available(
                request_based_available=(
                    request_based_available.cpu_millicores
                ),
                provider_capacity=(
                    resource.provider_capacity_cpu_millicores
                ),
                actual_usage=(
                    node_usage.cpu_millicores
                    if node_usage is not None
                    else None
                ),
            ),
            memory_mib=self._effective_available(
                request_based_available=request_based_available.memory_mib,
                provider_capacity=resource.provider_capacity_memory_mib,
                actual_usage=(
                    node_usage.memory_mib
                    if node_usage is not None
                    else None
                ),
            ),
            ephemeral_storage_mib=(
                request_based_available.ephemeral_storage_mib
            ),
        )
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
            runtime_cpu_usage_millicores=(
                node_usage.cpu_millicores if node_usage is not None else None
            ),
            runtime_memory_usage_mib=(
                node_usage.memory_mib if node_usage is not None else None
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
            node_usage=node_usage,
            allocatable_capacity=allocatable,
        )
