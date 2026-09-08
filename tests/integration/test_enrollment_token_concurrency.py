from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import AgentCertificateTable, AgentEnrollmentTokenTable
from app.services.agent_enrollment_service import (
    AgentEnrollmentService,
    InvalidEnrollmentTokenError,
    IssuedAgentCertificate,
)


class FakeCertificateAuthority:
    def __init__(self) -> None:
        self._lock = Lock()
        self.calls = 0

    def issue(
        self,
        *,
        resource_target_id: UUID,
        csr_pem: str,
    ) -> IssuedAgentCertificate:
        del resource_target_id, csr_pem
        with self._lock:
            self.calls += 1
            serial = self.calls
        now = datetime.now(UTC)
        return IssuedAgentCertificate(
            client_certificate_pem="test-client-certificate",
            client_ca_pem="test-client-ca",
            issuer_fingerprint_sha256="a" * 64,
            serial_number=str(serial),
            fingerprint_sha256=f"{serial:064x}",
            not_before=now - timedelta(minutes=1),
            not_after=now + timedelta(days=1),
        )


def _consume_token(
    session_factory: sessionmaker[Session],
    barrier: Barrier,
    *,
    token: str,
    resource_target_id: UUID,
    authority: FakeCertificateAuthority,
) -> str:
    with session_factory() as session:
        barrier.wait(timeout=10)
        try:
            AgentEnrollmentService(session).enroll(
                token=token,
                resource_target_id=resource_target_id,
                csr_pem="test-csr",
                certificate_authority=authority,
            )
        except InvalidEnrollmentTokenError:
            session.rollback()
            return "rejected"
        return "issued"


def test_one_time_enrollment_token_is_consumed_once_under_concurrency(
    db_session: Session,
    session_factory: sessionmaker[Session],
    eligible_resource_target_id: UUID,
) -> None:
    token = AgentEnrollmentService(db_session).issue_token(
        resource_target_id=eligible_resource_target_id,
        expires_in_seconds=600,
    ).token
    barrier = Barrier(2)
    authority = FakeCertificateAuthority()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _consume_token,
                session_factory,
                barrier,
                token=token,
                resource_target_id=eligible_resource_target_id,
                authority=authority,
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(results) == ["issued", "rejected"]
    assert authority.calls == 1
    with session_factory() as session:
        certificate_count = session.scalar(
            select(func.count(AgentCertificateTable.certificate_id))
        )
        enrollment = session.scalar(select(AgentEnrollmentTokenTable))
    assert certificate_count == 1
    assert enrollment is not None
    assert enrollment.used_at is not None
