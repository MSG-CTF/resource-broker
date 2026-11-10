from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.tables import GcpCreditObservationTable


class GcpCreditObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> tuple[GcpCreditObservationTable, ...]:
        statement = select(GcpCreditObservationTable).order_by(
            GcpCreditObservationTable.billing_account_id
        )
        return tuple(self._session.scalars(statement).all())

    def save(self, billing_account_id: str, amount: str) -> GcpCreditObservationTable:
        observed_at = datetime.now(UTC)
        statement = (
            insert(GcpCreditObservationTable)
            .values(
                billing_account_id=billing_account_id,
                amount=amount,
                observed_at=observed_at,
            )
            .on_conflict_do_update(
                index_elements=[GcpCreditObservationTable.billing_account_id],
                set_={"amount": amount, "observed_at": observed_at},
            )
            .returning(GcpCreditObservationTable)
        )
        return self._session.scalars(statement).one()
