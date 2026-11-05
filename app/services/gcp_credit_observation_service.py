from sqlalchemy.orm import Session

from app.db.tables import GcpCreditObservationTable
from app.repositories.gcp_credit_observations import GcpCreditObservationRepository


class GcpCreditObservationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._observations = GcpCreditObservationRepository(session)

    def list_all(self) -> tuple[GcpCreditObservationTable, ...]:
        return self._observations.list_all()

    def save(self, billing_account_id: str, amount: str) -> GcpCreditObservationTable:
        observation = self._observations.save(billing_account_id, amount)
        self._session.commit()
        return observation
