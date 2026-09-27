from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import K3sCredentialTable, ResourceTargetTable


class K3sCredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        resource_target_id: UUID,
        *,
        for_update: bool = False,
        runtime_visible_only: bool = False,
    ) -> K3sCredentialTable | None:
        statement = select(K3sCredentialTable).where(
            K3sCredentialTable.resource_target_id == resource_target_id
        )
        if runtime_visible_only:
            statement = statement.join(ResourceTargetTable).where(
                ResourceTargetTable.enabled.is_(True),
                ResourceTargetTable.retired_at.is_(None),
            )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, credential: K3sCredentialTable) -> None:
        self._session.add(credential)
        self._session.flush()

    def list_runtime_visible(
        self,
        *,
        after: UUID | None,
        limit: int,
    ) -> tuple[K3sCredentialTable, ...]:
        statement = (
            select(K3sCredentialTable)
            .join(ResourceTargetTable)
            .where(
                ResourceTargetTable.enabled.is_(True),
                ResourceTargetTable.retired_at.is_(None),
            )
            .order_by(K3sCredentialTable.resource_target_id)
            .limit(limit + 1)
        )
        if after is not None:
            statement = statement.where(
                K3sCredentialTable.resource_target_id > after
            )
        return tuple(self._session.scalars(statement).all())
