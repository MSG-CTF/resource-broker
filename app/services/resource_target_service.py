from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import ResourceTargetRepository


@dataclass(frozen=True, slots=True)
class ResourceTargetListOutcome:
    total: int
    resources: tuple[ResourceTargetTable, ...]


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
