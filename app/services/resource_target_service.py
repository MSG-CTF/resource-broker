from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable, RuntimeContainerTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import ResourceTargetRepository


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


class ResourceTargetService:
    def __init__(self, session: Session) -> None:
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
