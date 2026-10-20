import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.tables import ReservationTable, ResourceTargetTable
from app.domain.enums import Architecture, ReservationStatus, RuntimeType
from app.services.candidate_service import CandidateService
from app.services.reservation_service import CandidateUnavailableError, ReservationService


def configure(db_session, eligible_resource_target_id, monkeypatch, tmp_path):
    resource=db_session.get(ResourceTargetTable, eligible_resource_target_id)
    resource.runtime_type=RuntimeType.VM
    db_session.commit()
    challenge=uuid4()
    path=tmp_path/'vm.json'
    path.write_text(json.dumps({'schema_version':1,'placements':[{'target_id':resource.target_id,'challenge_ids':[str(challenge)]}]}))
    monkeypatch.setenv('BROKER_DEDICATED_VM_PLACEMENTS_FILE',str(path))
    return resource,challenge


def request(resource, challenge, instance=None):
    return dict(request_id='vm-'+str(uuid4()),requested_at=datetime.now(UTC),candidate_id=resource.resource_target_id,
      team_id=uuid4(),challenge_id=challenge,instance_id=str(instance or uuid4()),
      cpu_millicores=100,memory_mib=100,ephemeral_storage_mib=100,architecture=Architecture.AMD64)


def query(session, challenge):
    return CandidateService(session).query(challenge_id=challenge,cpu_millicores=100,memory_mib=100,
      ephemeral_storage_mib=100,architecture=Architecture.AMD64)


def test_vm_is_invisible_and_unreservable_to_other_challenges(db_session,eligible_resource_target_id,monkeypatch,tmp_path):
    resource,challenge=configure(db_session,eligible_resource_target_id,monkeypatch,tmp_path)
    assert len(query(db_session,challenge).candidates)==1
    assert not query(db_session,uuid4()).candidates
    with pytest.raises(CandidateUnavailableError):ReservationService(db_session).create(**request(resource,uuid4()))


def test_vm_exclusive_lease_is_not_hidden_by_new_observation(db_session,eligible_resource_target_id,monkeypatch,tmp_path):
    resource,challenge=configure(db_session,eligible_resource_target_id,monkeypatch,tmp_path)
    body=request(resource,challenge)
    service=ReservationService(db_session)
    first=service.create(**body)
    assert service.create(**body).replayed
    assert not query(db_session,challenge).candidates
    reservation=first.reservation
    reservation.status=ReservationStatus.COMMITTED
    reservation.committed_at=datetime.now(UTC)-timedelta(seconds=10)
    reservation.expires_at=None
    resource.runtime_observed_at=datetime.now(UTC)
    resource.runtime_last_seen_at=datetime.now(UTC)
    db_session.commit()
    assert not query(db_session,challenge).candidates
    with pytest.raises(CandidateUnavailableError):service.create(**request(resource,challenge))
    reservation.status=ReservationStatus.RELEASED
    reservation.released_at=datetime.now(UTC)
    db_session.commit()
    assert len(query(db_session,challenge).candidates)==1


def test_dedicated_challenge_does_not_fall_back_to_kubernetes(db_session,eligible_resource_target_id,monkeypatch,tmp_path):
    resource,challenge=configure(db_session,eligible_resource_target_id,monkeypatch,tmp_path)
    resource.runtime_type=RuntimeType.KUBERNETES
    db_session.commit()
    assert not query(db_session,challenge).candidates
    with pytest.raises(CandidateUnavailableError):ReservationService(db_session).create(**request(resource,challenge))


def test_agent_vm_report_without_operator_policy_is_not_schedulable(db_session,eligible_resource_target_id,monkeypatch):
    monkeypatch.delenv('BROKER_DEDICATED_VM_PLACEMENTS_FILE',raising=False)
    resource=db_session.get(ResourceTargetTable,eligible_resource_target_id)
    resource.runtime_type=RuntimeType.VM
    db_session.commit()
    challenge=uuid4()
    assert not query(db_session,challenge).candidates
    with pytest.raises(CandidateUnavailableError):ReservationService(db_session).create(**request(resource,challenge))


def test_concurrent_requests_cannot_share_one_vm(db_session,session_factory,eligible_resource_target_id,monkeypatch,tmp_path):
    resource,challenge=configure(db_session,eligible_resource_target_id,monkeypatch,tmp_path)
    requests=[request(resource,challenge),request(resource,challenge)]
    barrier=Barrier(2)

    def reserve(body):
        with session_factory() as session:
            service=ReservationService(session)
            barrier.wait(timeout=8)
            try:
                outcome=service.create(**body)
                return outcome.reservation.status.value
            except CandidateUnavailableError:
                session.rollback()
                return 'UNAVAILABLE'

    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(reserve,requests))
    assert sorted(results)==['HELD','UNAVAILABLE']
    rows=db_session.scalars(select(ReservationTable).where(ReservationTable.resource_target_id==resource.resource_target_id)).all()
    assert len(rows)==1 and rows[0].status==ReservationStatus.HELD


def test_expired_hold_replay_does_not_take_a_replacement_vm_lease(db_session,eligible_resource_target_id,monkeypatch,tmp_path):
    resource,challenge=configure(db_session,eligible_resource_target_id,monkeypatch,tmp_path)
    service=ReservationService(db_session)
    body=request(resource,challenge)
    first=service.create(**body).reservation
    first.expires_at=datetime.now(UTC)-timedelta(seconds=1)
    db_session.commit()
    replacement=service.create(**request(resource,challenge)).reservation
    assert replacement.reservation_id!=first.reservation_id
    assert replacement.status==ReservationStatus.HELD
    old=service.create(**body)
    assert old.replayed and old.reservation.status==ReservationStatus.EXPIRED
    assert not query(db_session,challenge).candidates
