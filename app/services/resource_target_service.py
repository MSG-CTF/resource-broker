from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable, RuntimeContainerTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import ResourceTargetRepository
from app.services.scheduler_settings import SchedulerSettings


@dataclass(frozen=True, slots=True)
class ResourceTargetListOutcome:
    total: int
    resources: tuple[ResourceTargetTable, ...]


@dataclass(frozen=True, slots=True)
class ResourceTargetContainerListOutcome:
    resource_target_id: UUID
    observed_at: datetime | None
    containers: tuple[RuntimeContainerTable, ...]


class ResourceTargetNotFoundError(LookupError):
    pass


class ResourceTargetNotEligibleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ResourceTargetService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._resources = ResourceTargetRepository(session)

    def list_resources(
        self,
        *,
        provider: Provider | None,
        account_id: UUID | None,
        include_retired: bool,
        limit: int,
        offset: int,
    ) -> ResourceTargetListOutcome:
        result = self._resources.list_all(
            provider=provider,
            account_id=account_id,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )
        return ResourceTargetListOutcome(
            total=result.total,
            resources=result.resources,
        )

    def list_containers(
        self,
        resource_target_id: UUID,
    ) -> ResourceTargetContainerListOutcome:
        resource = self._resources.get(resource_target_id)
        if resource is None:
            raise ResourceTargetNotFoundError

        return ResourceTargetContainerListOutcome(
            resource_target_id=resource.resource_target_id,
            observed_at=resource.runtime_observed_at,
            containers=self._resources.list_runtime_containers(
                resource_target_id
            ),
        )

    def set_enabled(
        self,
        resource_target_id: UUID,
        *,
        enabled: bool,
    ) -> ResourceTargetTable:
        resource = self._resources.get_for_update(resource_target_id)
        if resource is None:
            raise ResourceTargetNotFoundError
        if enabled:
            self._validate_candidate_registration(resource)
        resource.enabled = enabled
        self._session.commit()
        self._session.refresh(resource)
        return resource

    @staticmethod
    def _registration_error(
        code: str,
        message: str,
    ) -> ResourceTargetNotEligibleError:
        return ResourceTargetNotEligibleError(code, message)

    def _validate_candidate_registration(
        self,
        resource: ResourceTargetTable,
    ) -> None:
        if not resource.account.enabled:
            raise self._registration_error(
                "PROVIDER_ACCOUNT_DISABLED",
                "The Provider account is disabled.",
            )
        if resource.retired_at is not None:
            raise self._registration_error(
                "RESOURCE_TARGET_RETIRED",
                "The resource target is retired.",
            )
        if not resource.ready:
            raise self._registration_error(
                "RUNTIME_NOT_READY",
                "The Runtime is not ready.",
            )
        if resource.architecture is None:
            raise self._registration_error(
                "ARCHITECTURE_NOT_OBSERVED",
                "The VM architecture has not been observed.",
            )
        if resource.runtime_type is None or resource.target_id is None:
            raise self._registration_error(
                "RUNTIME_TARGET_NOT_OBSERVED",
                "The Runtime target has not been observed.",
            )

        last_seen_at = resource.runtime_last_seen_at
        if resource.runtime_observed_at is None or last_seen_at is None:
            raise self._registration_error(
                "RUNTIME_OBSERVATION_MISSING",
                "A Runtime observation has not been received.",
            )
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        stale_seconds = (
            SchedulerSettings.from_environment().observation_stale_seconds
        )
        if last_seen_at <= datetime.now(UTC) - timedelta(
            seconds=stale_seconds
        ):
            raise self._registration_error(
                "RUNTIME_OBSERVATION_STALE",
                "The latest Runtime observation is stale.",
            )

        if (
            resource.provider_capacity_cpu_millicores is None
            or resource.provider_capacity_memory_mib is None
        ):
            raise self._registration_error(
                "PROVIDER_CAPACITY_NOT_OBSERVED",
                "The Provider CPU or memory capacity has not been observed.",
            )
        if (
            resource.runtime_cpu_usage_millicores is None
            or resource.runtime_memory_usage_mib is None
        ):
            raise self._registration_error(
                "RUNTIME_USAGE_NOT_OBSERVED",
                "The VM CPU or memory usage has not been observed.",
            )
        if (
            resource.allocatable_cpu_millicores is None
            or resource.allocatable_memory_mib is None
            or resource.allocatable_ephemeral_storage_mib is None
        ):
            raise self._registration_error(
                "ALLOCATABLE_CAPACITY_NOT_OBSERVED",
                "The allocatable capacity has not been observed.",
            )
