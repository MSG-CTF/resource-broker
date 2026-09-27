from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import (
    ContainerStorageUsage,
    ResourceTargetRepository,
)
from app.repositories.reservations import CandidateRepository, ReservedCapacity


@dataclass(frozen=True, slots=True)
class InventoryListOutcome:
    generated_at: datetime
    total: int
    resources: tuple[ResourceTargetTable, ...]
    reservations_by_target: dict[UUID, ReservedCapacity]
    container_storage_by_target: dict[UUID, ContainerStorageUsage]


class InventoryService:
    def __init__(self, session: Session) -> None:
        self._resources = ResourceTargetRepository(session)
        self._reservations = CandidateRepository(session)

    def list_resources(
        self,
        *,
        provider: Provider | None,
        account_id: UUID | None,
        include_retired: bool,
        limit: int,
        offset: int,
    ) -> InventoryListOutcome:
        generated_at = datetime.now(UTC)
        page = self._resources.list_all(
            provider=provider,
            account_id=account_id,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )
        resource_target_ids = tuple(
            resource.resource_target_id for resource in page.resources
        )
        return InventoryListOutcome(
            generated_at=generated_at,
            total=page.total,
            resources=page.resources,
            reservations_by_target=self._reservations.deductible_usage_by_target(
                resource_target_ids=resource_target_ids,
                now=generated_at,
            ),
            container_storage_by_target=(
                self._resources.container_storage_usage_by_target(resource_target_ids)
            ),
        )
