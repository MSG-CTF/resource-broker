from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.enums import Architecture, ReleaseReason, ReservationStatus
from app.repositories.reservations import CandidateRepository, ReservationRepository
from app.schemas.reservation import ReservationCommitRequest
from app.services.reservation_service import (
    DeployedSpecMismatchError,
    InsufficientCandidateCapacityError,
    ReservationMutationConflictError,
    ReservationService,
)
from app.services.scheduler_settings import SchedulerSettings
from app.services.allocation_snapshot import allocation_snapshots


SETTINGS = SchedulerSettings(observation_stale_seconds=600, candidate_validity_seconds=30, reservation_ttl_seconds=120)


def intent(target):
    return dict(request_id=str(uuid4()), requested_at=datetime.now(UTC),
                candidate_id=target, team_id=uuid4(), challenge_id=uuid4(),
                instance_id=str(uuid4()), cpu_millicores=600, memory_mib=100,
                ephemeral_storage_mib=100, architecture=Architecture.AMD64)


def commit_for(reservation, **overrides):
    return dict(request_id='commit-'+str(reservation.reservation_id),
                requested_at=datetime.now(UTC), instance_id=reservation.instance_id,
                runtime_workload_id='runtime/'+reservation.instance_id,
                cpu_millicores=100, memory_mib=100, ephemeral_storage_mib=100,
                startup_cpu_millicores=600) | overrides


def charged(session, target):
    now = datetime.now(UTC)
    a = ReservationRepository(session).deductible_usage_for_target(resource_target_id=target, now=now)
    b = CandidateRepository(session).deductible_usage_by_target(resource_target_ids=(target,), now=now)
    assert a.cpu_millicores == (b[target].cpu_millicores if target in b else 0)
    return a.cpu_millicores


def test_peak_is_held_until_explicit_commit_then_create_and_commit_replay_are_stable(db_session, eligible_resource_target_id):
    target = eligible_resource_target_id
    service = ReservationService(db_session, SETTINGS)
    original = intent(target)
    reservation = service.create(**original).reservation
    assert charged(db_session, target) == 600
    assert allocation_snapshots(db_session, (target,))[target]['allocations'][0]['resources']['cpu_millicores'] == 600
    with pytest.raises(InsufficientCandidateCapacityError):
        service.create(**intent(target))
    db_session.rollback()
    commit = commit_for(reservation)
    saved = service.commit(reservation.reservation_id, **commit)
    assert saved.status is ReservationStatus.COMMITTED
    assert saved.cpu_millicores == 600 and saved.deployed_cpu_millicores == 100
    assert charged(db_session, target) == 100
    assert allocation_snapshots(db_session, (target,))[target]['allocations'][0]['resources']['cpu_millicores'] == 100
    assert service.create(**original).replayed
    assert service.commit(reservation.reservation_id, **commit).reservation_id == saved.reservation_id
    second = service.create(**intent(target)).reservation
    assert charged(db_session, target) == 700
    for row in (reservation, second):
        release = dict(request_id='release-'+str(row.reservation_id), requested_at=datetime.now(UTC),
                       instance_id=row.instance_id, release_reason=ReleaseReason.SCHEDULER_CANCELLED)
        assert service.release(row.reservation_id, **release).status is ReservationStatus.RELEASED
        assert service.release(row.reservation_id, **release).status is ReservationStatus.RELEASED
    assert charged(db_session, target) == 0


@pytest.mark.parametrize('changes', [
    {'startup_cpu_millicores': None}, {'startup_cpu_millicores': 601},
    {'cpu_millicores': 600}, {'cpu_millicores': 700},
    {'memory_mib': 99}, {'ephemeral_storage_mib': 99},
])
def test_unmarked_or_unapproved_shrink_keeps_peak_reserved(db_session, eligible_resource_target_id, changes):
    service = ReservationService(db_session, SETTINGS)
    row = service.create(**intent(eligible_resource_target_id)).reservation
    with pytest.raises(DeployedSpecMismatchError):
        service.commit(row.reservation_id, **commit_for(row, **changes))
    db_session.rollback()
    assert service.get(row.reservation_id).status is ReservationStatus.HELD
    assert charged(db_session, eligible_resource_target_id) == 600


def test_changed_commit_marker_cannot_replay_an_already_committed_shrink(db_session, eligible_resource_target_id):
    service = ReservationService(db_session, SETTINGS)
    row = service.create(**intent(eligible_resource_target_id)).reservation
    commit = commit_for(row)
    service.commit(row.reservation_id, **commit)
    for marker in (None, 500):
        with pytest.raises(ReservationMutationConflictError):
            service.commit(row.reservation_id, **(commit | {'startup_cpu_millicores': marker}))
        db_session.rollback()
    assert charged(db_session, eligible_resource_target_id) == 100


def test_legacy_commit_still_requires_exact_cpu_and_keeps_original_accounting(db_session, eligible_resource_target_id):
    service = ReservationService(db_session, SETTINGS)
    row = service.create(**intent(eligible_resource_target_id)).reservation
    commit = commit_for(row, cpu_millicores=600, startup_cpu_millicores=None)
    assert service.commit(row.reservation_id, **commit).deployed_cpu_millicores == 600
    assert charged(db_session, eligible_resource_target_id) == 600
    assert service.commit(row.reservation_id, **commit).status is ReservationStatus.COMMITTED


@pytest.mark.parametrize('value', [0, -1, True, '600', 600.5])
def test_commit_startup_cpu_is_a_strict_positive_integer(value):
    with pytest.raises(ValidationError):
        ReservationCommitRequest.model_validate(dict(request_id='test', requested_at=datetime.now(UTC),
            instance_id='test', reservation_id=uuid4(), runtime_workload_id='test',
            resource_profile={'cpu_millicores': 100, 'memory_mib': 100, 'ephemeral_storage_mib': 100},
            startup_cpu_millicores=value))
