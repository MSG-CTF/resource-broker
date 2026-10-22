from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.db.tables import ReservationTable, ResourceTargetTable
from app.main import app


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setenv("INVENTORY_API_TOKEN", "inventory-local-qa-token-000000000000")
    monkeypatch.setenv("SCHEDULER_API_TOKEN", "scheduler-local-qa-token-000000000000")
    app.dependency_overrides[get_db_session] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


INVENTORY = {"Authorization": "Bearer inventory-local-qa-token-000000000000"}
SCHEDULER = {"Authorization": "Bearer scheduler-local-qa-token-000000000000"}
URL = "/v1/inventory/resource-targets?include_allocations=true"


def reserve(client, target):
    body = {"request_id": str(uuid4()), "requested_at": datetime.now(UTC).isoformat(),
            "instance_id": str(uuid4()), "candidate_id": str(target),
            "team_id": str(uuid4()), "challenge_id": str(uuid4()), "architecture": "AMD64",
            "resource_profile": {"cpu_millicores": 200, "memory_mib": 300, "ephemeral_storage_mib": 100}}
    response = client.post("/v1/reservations", headers=SCHEDULER, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def snapshot(client):
    response = client.get(URL, headers=INVENTORY)
    assert response.status_code == 200, response.text
    return response.json()["items"][0]["resource_allocation"]


def test_real_reserve_commit_observe_release_lifecycle(client, db_session, eligible_resource_target_id):
    assert snapshot(client)["allocations"] == []
    reservation, body = reserve(client, eligible_resource_target_id)
    allocation = snapshot(client)
    assert allocation["complete"] is True
    assert allocation["allocations"][0]["reservation_status"] == "HELD"
    assert allocation["allocations"][0]["resources"] == {"cpu_millicores": 200, "memory_mib": 300}
    rid = reservation["reservation_id"]
    mutation = {"request_id": str(uuid4()), "requested_at": datetime.now(UTC).isoformat(),
                "instance_id": body["instance_id"], "reservation_id": rid}
    response = client.post(f"/v1/reservations/{rid}/commit", headers=SCHEDULER,
                           json={**mutation, "runtime_workload_id": "qa-workload", "resource_profile": body["resource_profile"]})
    assert response.status_code == 200, response.text
    target = db_session.get(ResourceTargetTable, eligible_resource_target_id)
    target.runtime_observed_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    assert snapshot(client)["allocations"][0]["reservation_status"] == "COMMITTED"
    mutation["request_id"] = str(uuid4())
    response = client.post(f"/v1/reservations/{rid}/release", headers=SCHEDULER,
                           json={**mutation, "release_reason": "SCHEDULER_CANCELLED"})
    assert response.status_code == 200, response.text
    assert snapshot(client)["allocations"] == []


def test_expired_hold_is_not_occupied(client, db_session, eligible_resource_target_id):
    reservation, _ = reserve(client, eligible_resource_target_id)
    from uuid import UUID
    row = db_session.get(ReservationTable, UUID(reservation["reservation_id"]))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert snapshot(client)["allocations"] == []


def test_unknown_capacity_is_not_zero(client, db_session, eligible_resource_target_id):
    target = db_session.get(ResourceTargetTable, eligible_resource_target_id)
    target.node_allocatable_cpu_millicores = None
    db_session.commit()
    assert snapshot(client)["allocatable"]["cpu_millicores"] is None


def test_allocations_are_opt_in_and_inventory_protected(client, eligible_resource_target_id):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=SCHEDULER).status_code == 401
    response = client.get("/v1/inventory/resource-targets", headers=INVENTORY)
    assert response.status_code == 200
    assert "resource_allocation" not in response.json()["items"][0]


def test_legacy_identifier_does_not_hide_reserved_capacity(client, db_session, eligible_resource_target_id):
    from uuid import UUID
    reservation, _ = reserve(client, eligible_resource_target_id)
    row = db_session.get(ReservationTable, UUID(reservation["reservation_id"]))
    row.instance_id = "legacy-instance"
    db_session.commit()
    record = snapshot(client)["allocations"][0]
    assert record["instance_id"] is None
    assert record["reservation_id"] == reservation["reservation_id"]
    assert record["resources"]["cpu_millicores"] == 200


def test_observed_usage_changes_availability_without_shrinking_reservation_budget(client, db_session, eligible_resource_target_id):
    from app.domain.enums import RuntimeType
    from app.services.agent_observation_service import AgentObservationService, ObservationCapacity, ObservationUsage

    reserve(client, eligible_resource_target_id)
    # Old rows remain unknown instead of reusing already-reduced availability.
    assert snapshot(client)["allocatable"]["cpu_millicores"] is None
    for used in (800, 1200):
        AgentObservationService(db_session).record(
            resource_target_id=eligible_resource_target_id,
            observed_at=datetime.now(UTC), snapshot_complete=True,
            runtime_type=RuntimeType.KUBERNETES,
            runtime_target_id=f"node-{eligible_resource_target_id}", runtime_ready=True,
            node_allocatable=ObservationCapacity(2000, 4096, 20000),
            allocated_requests=ObservationCapacity(used, 1024, 1000),
            containers=(), node_usage=ObservationUsage(used, 1024),
        )
        target = db_session.get(ResourceTargetTable, eligible_resource_target_id)
        assert target.allocatable_cpu_millicores == 2000 - used
        current = snapshot(client)
        assert current["allocatable"] == {"cpu_millicores": 2000, "memory_mib": 4096}
        assert current["allocations"][0]["resources"]["cpu_millicores"] == 200
