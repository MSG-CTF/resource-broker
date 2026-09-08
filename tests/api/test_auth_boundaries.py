from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.admin_auth import (
    AdminAuthHttpError,
    admin_auth_http_error_handler,
    router as admin_auth_router,
)
from app.api.admin_provider_accounts import router as admin_accounts_router
from app.api.agent_enrollments import router as agent_enrollments_router
from app.api.agent_observations import router as agent_observations_router
from app.api.reservations import router as reservations_router
from app.api.scheduler_auth import (
    SchedulerAuthHttpError,
    scheduler_auth_http_error_handler,
)
from app.db.session import get_db_session


ADMIN_ID = "test-admin"
ADMIN_PASSWORD = "test-admin-password"
ADMIN_SECRET = "a" * 32
SCHEDULER_TOKEN = "s" * 40


@pytest.fixture
def api_client(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ADMIN_ID", ADMIN_ID)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_TOKEN_SECRET", ADMIN_SECRET)
    monkeypatch.setenv("SCHEDULER_API_TOKEN", SCHEDULER_TOKEN)

    app = FastAPI()
    app.add_exception_handler(AdminAuthHttpError, admin_auth_http_error_handler)
    app.add_exception_handler(
        SchedulerAuthHttpError,
        scheduler_auth_http_error_handler,
    )
    app.include_router(admin_auth_router)
    app.include_router(admin_accounts_router)
    app.include_router(reservations_router)
    app.include_router(agent_observations_router)
    app.include_router(agent_enrollments_router)

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    with TestClient(app) as client:
        yield client


def _admin_token(client: TestClient) -> str:
    response = client.post(
        "/v1/admin/auth/login",
        json={"admin_id": ADMIN_ID, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_admin_and_scheduler_tokens_cannot_cross_boundaries(
    api_client: TestClient,
) -> None:
    admin_token = _admin_token(api_client)

    admin_with_scheduler_token = api_client.get(
        "/v1/admin/provider-accounts",
        headers={"Authorization": f"Bearer {SCHEDULER_TOKEN}"},
    )
    assert admin_with_scheduler_token.status_code == 401
    assert admin_with_scheduler_token.json()["error"]["code"] == (
        "INVALID_ADMIN_TOKEN"
    )

    scheduler_with_admin_token = api_client.get(
        f"/v1/reservations/{uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert scheduler_with_admin_token.status_code == 401
    assert scheduler_with_admin_token.json()["error"]["code"] == (
        "INVALID_SCHEDULER_TOKEN"
    )

    valid_admin = api_client.get(
        "/v1/admin/provider-accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert valid_admin.status_code == 200

    valid_scheduler = api_client.get(
        f"/v1/reservations/{uuid4()}",
        headers={"Authorization": f"Bearer {SCHEDULER_TOKEN}"},
    )
    assert valid_scheduler.status_code == 404
    assert valid_scheduler.json()["error"]["code"] == "RESERVATION_NOT_FOUND"


def test_agent_observation_requires_verified_matching_identity(
    api_client: TestClient,
) -> None:
    resource_target_id = uuid4()
    payload = {
        "resource_target_id": str(resource_target_id),
        "observed_at": datetime.now(UTC).isoformat(),
        "snapshot_complete": True,
        "runtime": {
            "type": "KUBERNETES",
            "target_id": "test-node",
            "ready": True,
        },
        "node_allocatable": {
            "cpu_millicores": 1_000,
            "memory_mib": 1_000,
            "ephemeral_storage_mib": 1_000,
        },
        "allocated_requests": {
            "cpu_millicores": 0,
            "memory_mib": 0,
            "ephemeral_storage_mib": 0,
        },
        "containers": [],
    }

    missing_certificate = api_client.post(
        "/v1/agent/observations",
        json=payload,
    )
    assert missing_certificate.status_code == 401
    assert missing_certificate.json()["error"]["code"] == (
        "AGENT_CERTIFICATE_REQUIRED"
    )

    wrong_identity = api_client.post(
        "/v1/agent/observations",
        json=payload,
        headers={
            "X-Agent-Cert-Verify": "SUCCESS",
            "X-Agent-Cert-Fingerprint": "test-fingerprint",
            "X-Agent-Resource-Target-ID": str(uuid4()),
        },
    )
    assert wrong_identity.status_code == 403
    assert wrong_identity.json()["error"]["code"] == (
        "AGENT_RESOURCE_TARGET_MISMATCH"
    )


def test_certificate_enrollment_requires_one_time_bearer_token(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/v1/agent/enrollments",
        json={
            "resource_target_id": str(uuid4()),
            "certificate_signing_request_pem": "x" * 128,
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ENROLLMENT_TOKEN_REQUIRED"
