from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import ReservationTable
from app.domain.enums import Architecture
from app.services.reservation_service import (
    InsufficientCandidateCapacityError,
    ReservationService,
)
from app.services.scheduler_settings import SchedulerSettings


SETTINGS = SchedulerSettings(
    observation_stale_seconds=600,
    candidate_validity_seconds=30,
    reservation_ttl_seconds=120,
)


def _create_reservation(
    session_factory: sessionmaker[Session],
    barrier: Barrier,
    *,
    request_id: str,
    requested_at: datetime,
    candidate_id: UUID,
    team_id: UUID,
    challenge_id: UUID,
    instance_id: str,
) -> tuple[str, UUID | None, bool | None]:
    with session_factory() as session:
        barrier.wait(timeout=10)
        try:
            outcome = ReservationService(session, SETTINGS).create(
                request_id=request_id,
                requested_at=requested_at,
                candidate_id=candidate_id,
                team_id=team_id,
                challenge_id=challenge_id,
                instance_id=instance_id,
                cpu_millicores=600,
                memory_mib=600,
                ephemeral_storage_mib=600,
                architecture=Architecture.AMD64,
            )
        except InsufficientCandidateCapacityError:
            session.rollback()
            return "insufficient", None, None
        return (
            "created",
            outcome.reservation.reservation_id,
            outcome.replayed,
        )


def test_concurrent_reservations_cannot_oversubscribe_one_target(
    session_factory: sessionmaker[Session],
    eligible_resource_target_id: UUID,
) -> None:
    barrier = Barrier(2)
    requested_at = datetime.now(UTC)
    challenge_id = uuid4()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _create_reservation,
                session_factory,
                barrier,
                request_id=f"reserve-{index}-{uuid4()}",
                requested_at=requested_at,
                candidate_id=eligible_resource_target_id,
                team_id=uuid4(),
                challenge_id=challenge_id,
                instance_id=f"instance-{index}",
            )
            for index in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(result[0] for result in results) == [
        "created",
        "insufficient",
    ]
    with session_factory() as session:
        count, cpu, memory, storage = session.execute(
            select(
                func.count(ReservationTable.reservation_id),
                func.sum(ReservationTable.cpu_millicores),
                func.sum(ReservationTable.memory_mib),
                func.sum(ReservationTable.ephemeral_storage_mib),
            )
        ).one()
    assert (count, cpu, memory, storage) == (1, 600, 600, 600)


def test_concurrent_identical_request_is_created_once_and_replayed(
    session_factory: sessionmaker[Session],
    eligible_resource_target_id: UUID,
) -> None:
    barrier = Barrier(2)
    request_id = f"idempotent-{uuid4()}"
    requested_at = datetime.now(UTC)
    team_id = uuid4()
    challenge_id = uuid4()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _create_reservation,
                session_factory,
                barrier,
                request_id=request_id,
                requested_at=requested_at,
                candidate_id=eligible_resource_target_id,
                team_id=team_id,
                challenge_id=challenge_id,
                instance_id="same-instance",
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert [result[0] for result in results] == ["created", "created"]
    assert len({result[1] for result in results}) == 1
    assert sorted(result[2] for result in results) == [False, True]
    with session_factory() as session:
        count = session.scalar(select(func.count(ReservationTable.reservation_id)))
    assert count == 1
