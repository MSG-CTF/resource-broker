from uuid import UUID

from sqlalchemy import select
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

    def execution(self, execution_id: UUID):
        return self.session.get(WindowsVmExecutionTable, execution_id)

    def add(self, row):
        self.session.add(row)

    def available(self, *, after: UUID | None, limit: int):
        connection = WindowsVmConnectionTable
        target = ResourceTargetTable
        account = ProviderAccountTable
        statement = (
            select(target, connection)
            .join(connection, connection.resource_target_id == target.resource_target_id)
            .join(account, account.account_id == target.account_id)
            .where(
                target.provider == Provider.AZURE,
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
