from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.tables import BootstrapJobTable, ResourceTargetTable
from app.domain.enums import BootstrapJobStatus


ACTIVE_STATUSES = (
    BootstrapJobStatus.QUEUED,
    BootstrapJobStatus.APPLYING,
    BootstrapJobStatus.RUNNING,
)


class BootstrapJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resource_target(self, resource_target_id: UUID) -> ResourceTargetTable | None:
        statement = (
            select(ResourceTargetTable)
            .options(joinedload(ResourceTargetTable.account))
            .where(ResourceTargetTable.resource_target_id == resource_target_id)
        )
        return self._session.scalar(statement)

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

    def next_active(self) -> BootstrapJobTable | None:
        statement = (
            select(BootstrapJobTable)
            .where(BootstrapJobTable.status.in_(ACTIVE_STATUSES))
            .order_by(BootstrapJobTable.updated_at, BootstrapJobTable.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return self._session.scalar(statement)
