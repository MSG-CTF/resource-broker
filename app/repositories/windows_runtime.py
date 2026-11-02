from uuid import UUID

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.tables import ProviderAccountTable, ResourceTargetTable
from app.db.windows_runtime import WindowsVmConnectionTable, WindowsVmExecutionTable
from app.domain.enums import Provider


class WindowsRuntimeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def target(self, resource_target_id: UUID, *, for_update: bool = False):
        statement = select(ResourceTargetTable).where(ResourceTargetTable.resource_target_id == resource_target_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def account(self, account_id: UUID):
        return self.session.get(ProviderAccountTable, account_id)

    def connection(self, resource_target_id: UUID):
        return self.session.get(WindowsVmConnectionTable, resource_target_id)

    def connections(self):
        return self.session.scalars(
            select(WindowsVmConnectionTable).order_by(WindowsVmConnectionTable.resource_target_id)
        ).all()

    def execution(self, execution_id: UUID):
        return self.session.get(WindowsVmExecutionTable, execution_id)

    def add(self, row):
        self.session.add(row)

    def pending_reset(self, target_ids: tuple[UUID, ...], *, finished_before: datetime):
        if not target_ids:
            return None
        connection = WindowsVmConnectionTable
        execution = WindowsVmExecutionTable
        target = ResourceTargetTable
        account = ProviderAccountTable
        return self.session.execute(
            select(target, account, connection, execution)
            .join(connection, connection.resource_target_id == target.resource_target_id)
            .join(account, account.account_id == target.account_id)
            .join(execution, execution.execution_id == connection.current_execution_id)
            .where(
                target.resource_target_id.in_(target_ids),
                account.provider == Provider.AZURE,
                target.os_type == "WINDOWS",
                connection.state == "RESET_REQUIRED",
                execution.finished_at <= finished_before,
            )
            .order_by(connection.updated_at, target.resource_target_id)
            .limit(1)
            .with_for_update(of=target, skip_locked=True)
        ).first()

    def pending_recovery(self, now: datetime):
        execution = WindowsVmExecutionTable
        return self.session.scalar(
            select(execution)
            .where(
                execution.recovery_event_id.is_not(None),
                execution.recovery_delivered_at.is_(None),
                or_(execution.recovery_retry_at.is_(None), execution.recovery_retry_at <= now),
            )
            .order_by(execution.finished_at, execution.execution_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def available(self, *, after: UUID | None, limit: int):
        connection = WindowsVmConnectionTable
        target = ResourceTargetTable
        account = ProviderAccountTable
        statement = (
            select(target, connection)
            .join(connection, connection.resource_target_id == target.resource_target_id)
            .join(account, account.account_id == target.account_id)
            .where(
                account.provider == Provider.AZURE,
                target.os_type == "WINDOWS",
                target.enabled.is_(True),
                account.enabled.is_(True),
                target.retired_at.is_(None),
                target.provider_instance_state == "RUNNING",
                connection.enabled.is_(True),
                connection.state == "READY",
            )
            .order_by(target.resource_target_id)
            .limit(limit + 1)
        )
        if after is not None:
            statement = statement.where(target.resource_target_id > after)
        return self.session.execute(statement).all()
