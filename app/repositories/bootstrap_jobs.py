from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.tables import (
    BootstrapJobTable,
    ProviderAccountTable,
    ResourceTargetTable,
)
from app.domain.enums import BootstrapJobStatus, Provider


ACTIVE_STATUSES = (
    BootstrapJobStatus.QUEUED,
    BootstrapJobStatus.APPLYING,
    BootstrapJobStatus.RUNNING,
)


class BootstrapJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resource_target(
        self,
        resource_target_id: UUID,
    ) -> ResourceTargetTable | None:
        statement = (
            select(ResourceTargetTable)
            .options(joinedload(ResourceTargetTable.account, innerjoin=True))
            .where(ResourceTargetTable.resource_target_id == resource_target_id)
        )
        return self._session.scalar(statement)

    def lock_provider_account(self, account_id: UUID) -> bool:
        statement = (
            select(ProviderAccountTable.account_id)
            .where(ProviderAccountTable.account_id == account_id)
            .with_for_update()
        )
        return self._session.scalar(statement) is not None

    def add(self, job: BootstrapJobTable) -> BootstrapJobTable:
        self._session.add(job)
        self._session.flush()
        return job

    def get(self, job_id: UUID, *, for_update: bool = False) -> BootstrapJobTable | None:
        statement = select(BootstrapJobTable).where(BootstrapJobTable.job_id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_for_resource(self, resource_target_id: UUID) -> tuple[BootstrapJobTable, ...]:
        statement = (
            select(BootstrapJobTable)
            .where(BootstrapJobTable.resource_target_id == resource_target_id)
            .order_by(BootstrapJobTable.created_at.desc(), BootstrapJobTable.job_id.desc())
            .limit(50)
        )
        return tuple(self._session.scalars(statement).all())

    def active_for_resource(self, resource_target_id: UUID) -> BootstrapJobTable | None:
        statement = (
            select(BootstrapJobTable)
            .where(
                BootstrapJobTable.resource_target_id == resource_target_id,
                BootstrapJobTable.status.in_(ACTIVE_STATUSES),
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def has_active_for_account(self, account_id: UUID) -> bool:
        statement = (
            select(BootstrapJobTable.job_id)
            .join(ResourceTargetTable)
            .where(
                ResourceTargetTable.account_id == account_id,
                BootstrapJobTable.status.in_(ACTIVE_STATUSES),
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    def active_assignment_count(self, project_id: str, zone: str) -> int:
        statement = (
            select(func.count())
            .select_from(BootstrapJobTable)
            .join(ResourceTargetTable)
            .where(
                ResourceTargetTable.provider_scope_id == project_id,
                ResourceTargetTable.zone == zone,
                BootstrapJobTable.provider == Provider.GCP,
                BootstrapJobTable.status.in_(
                    (
                        BootstrapJobStatus.APPLYING,
                        BootstrapJobStatus.RUNNING,
                    )
                ),
            )
        )
        return self._session.scalar(statement) or 0

    def next_active(self) -> BootstrapJobTable | None:
        statement = (
            select(BootstrapJobTable)
            .where(BootstrapJobTable.status.in_(ACTIVE_STATUSES))
            .order_by(BootstrapJobTable.updated_at, BootstrapJobTable.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return self._session.scalar(statement)
