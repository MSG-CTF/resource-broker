from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.tables import (
    AgentCertificateTable,
    AgentEnrollmentTokenTable,
    ResourceTargetTable,
)


class AgentEnrollmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resource_target(
        self,
        resource_target_id: UUID,
    ) -> ResourceTargetTable | None:
        statement = select(ResourceTargetTable).where(
            ResourceTargetTable.resource_target_id == resource_target_id
        )
        return self._session.scalar(statement)

    def revoke_unused_tokens(
        self,
        *,
        resource_target_id: UUID,
        revoked_at: datetime,
    ) -> None:
        statement = (
            update(AgentEnrollmentTokenTable)
            .where(
                AgentEnrollmentTokenTable.resource_target_id
                == resource_target_id,
                AgentEnrollmentTokenTable.used_at.is_(None),
                AgentEnrollmentTokenTable.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        self._session.execute(statement)

    def add_token(self, token: AgentEnrollmentTokenTable) -> None:
        self._session.add(token)

    def get_token_for_update(
        self,
        token_hash: str,
    ) -> AgentEnrollmentTokenTable | None:
        statement = (
            select(AgentEnrollmentTokenTable)
            .where(AgentEnrollmentTokenTable.token_hash == token_hash)
            .with_for_update()
        )
        return self._session.scalar(statement)

    def add_certificate(self, certificate: AgentCertificateTable) -> None:
        self._session.add(certificate)
