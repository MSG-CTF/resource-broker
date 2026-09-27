import base64
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.admin_auth import (
    AdminAuthHttpError,
    admin_auth_http_error_handler,
    router as admin_auth_router,
)
from app.api.admin_bootstrap_jobs import router as admin_bootstrap_router
from app.api.agent_k3s_credentials import router as upload_router
from app.api.runtime_auth import (
    RuntimeAuthHttpError,
    runtime_auth_http_error_handler,
)
from app.api.runtime_k3s_credentials import router as runtime_router
from app.db.session import get_db_session
from app.db.tables import (
    BootstrapJobTable,
    K3sCredentialTable,
    ResourceTargetTable,
)
from app.domain.enums import BootstrapJobStatus
from app.services.bootstrap_artifacts import BootstrapArtifact
from app.services.bootstrap_job_service import render_job_runner


ADMIN_ID = "test-admin"
ADMIN_PASSWORD = "test-admin-password"
ADMIN_SECRET = "a" * 32
UPLOAD_TOKEN_SECRET = "u" * 40
ENCRYPTION_SECRET = "e" * 40
RUNTIME_TOKEN_SECRET = "r" * 40
AGENT_IMAGE = "example.invalid/agent@sha256:" + "b" * 64


@pytest.fixture
def credential_client(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ADMIN_ID", ADMIN_ID)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("ADMIN_TOKEN_SECRET", ADMIN_SECRET)
    monkeypatch.setenv(
        "BOOTSTRAP_PUBLIC_BASE_URL",
        "https://agents.example.invalid",
    )
    monkeypatch.setenv("K3S_ADDRESS_SOURCE", "public_ip")
    monkeypatch.setenv("RUNTIME_API_TOKEN_SECRET", RUNTIME_TOKEN_SECRET)
    monkeypatch.setenv("RUNTIME_API_TOKEN_SUBJECT", "runtime")
    monkeypatch.delenv(
        "K3S_CREDENTIAL_UPLOAD_TOKEN_SECRET",
        raising=False,
    )
    monkeypatch.delenv(
        "K3S_CREDENTIAL_ENCRYPTION_SECRET",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.bootstrap_job_service.artifact_for_version",
        lambda version: BootstrapArtifact(
            path=Path(f"bootstrap-{version}.tar.gz"),
            sha256="c" * 64,
            public_url=(
                "https://agents.example.invalid/v1/agent/"
                f"bootstrap-artifacts/bootstrap-{version}.tar.gz"
            ),
        ),
    )

    app = FastAPI()
    app.add_exception_handler(
        AdminAuthHttpError,
        admin_auth_http_error_handler,
    )
    app.add_exception_handler(
        RuntimeAuthHttpError,
        runtime_auth_http_error_handler,
    )
    app.include_router(admin_auth_router)
    app.include_router(admin_bootstrap_router)
    app.include_router(upload_router)
    app.include_router(runtime_router)

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


def _runtime_token(secret: str = RUNTIME_TOKEN_SECRET) -> str:
    now = datetime.now(UTC).replace(microsecond=0)
    return jwt.encode(
        {
            "iss": "msg-runtime",
            "aud": "msg-broker-k3s-credentials",
            "type": "runtime_access",
            "sub": "runtime",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()) - 5,
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": str(uuid4()),
        },
        secret,
        algorithm="HS256",
    )


def _request_job(
    client: TestClient,
    resource_target_id: UUID,
    admin_token: str,
):
    return client.post(
        f"/v1/admin/resource-targets/{resource_target_id}/bootstrap-jobs",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "action": "INSTALL",
            "bootstrap_version": "0.1.0",
            "k3s_version": "v1.33.3+k3s1",
            "agent_image": AGENT_IMAGE,
        },
    )


def _kubeconfig(server_url: str) -> tuple[str, dict[str, object]]:
    now = datetime.now(UTC).replace(microsecond=0)
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "system:admin"),
            x509.NameAttribute(
                NameOID.ORGANIZATION_NAME,
                "system:masters",
            ),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(private_key, hashes.SHA256())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certificate_data = base64.b64encode(certificate_pem).decode("ascii")
    private_key_data = base64.b64encode(private_key_pem).decode("ascii")
    kubeconfig = (
        "apiVersion: v1\n"
        "clusters:\n"
        "- cluster:\n"
        f"    certificate-authority-data: {certificate_data}\n"
        f"    server: {server_url}\n"
        "  name: default\n"
        "contexts:\n"
        "- context:\n"
        "    cluster: default\n"
        "    user: default\n"
        "  name: default\n"
        "current-context: default\n"
        "kind: Config\n"
        "preferences: {}\n"
        "users:\n"
        "- name: default\n"
        "  user:\n"
        f"    client-certificate-data: {certificate_data}\n"
        f"    client-key-data: {private_key_data}\n"
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "server_url": server_url,
        "kubeconfig_base64": base64.b64encode(
            kubeconfig.encode("utf-8")
        ).decode("ascii"),
        "client_certificate_fingerprint_sha256": (
            certificate.fingerprint(hashes.SHA256()).hex()
        ),
        "client_certificate_not_after": (
            certificate.not_valid_after_utc.isoformat().replace(
                "+00:00",
                "Z",
            )
        ),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
    }
    return kubeconfig, payload


def test_bootstrap_uploads_encrypted_credential_and_runtime_pulls_it(
    credential_client: TestClient,
    db_session: Session,
    eligible_resource_target_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = db_session.get(
        ResourceTargetTable,
        eligible_resource_target_id,
    )
    assert target is not None
    target.public_ip = "192.0.2.10"
    db_session.commit()
    admin_token = _admin_token(credential_client)

    not_configured = _request_job(
        credential_client,
        eligible_resource_target_id,
        admin_token,
    )
    assert not_configured.status_code == 503
    assert not_configured.json()["error"]["code"] == (
        "K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED"
    )

    monkeypatch.setenv(
        "K3S_CREDENTIAL_UPLOAD_TOKEN_SECRET",
        UPLOAD_TOKEN_SECRET,
    )
    monkeypatch.setenv(
        "K3S_CREDENTIAL_ENCRYPTION_SECRET",
        ENCRYPTION_SECRET,
    )
    created = _request_job(
        credential_client,
        eligible_resource_target_id,
        admin_token,
    )
    assert created.status_code == 202
    job_id = UUID(created.json()["job_id"])
    job = db_session.get(BootstrapJobTable, job_id)
    assert job is not None
    job.status = BootstrapJobStatus.RUNNING
    db_session.commit()

    script = render_job_runner(job)
    assert "K3S_CREDENTIAL_UPLOAD_REQUIRED=true" in script
    assert (
        "K3S_CREDENTIAL_UPLOAD_URL="
        f"https://agents.example.invalid/v1/agent/bootstrap-jobs/"
        f"{job_id}/k3s-credentials"
    ) in script
    assert UPLOAD_TOKEN_SECRET not in script
    assert ENCRYPTION_SECRET not in script
    token_match = re.search(
        r"^K3S_CREDENTIAL_UPLOAD_TOKEN=([^\r\n]+)$",
        script,
        re.MULTILINE,
    )
    assert token_match is not None
    upload_token = token_match.group(1)
    upload_claims = jwt.decode(
        upload_token,
        UPLOAD_TOKEN_SECRET,
        algorithms=["HS256"],
        audience="msg-broker-k3s-credential-upload",
        issuer="msg-broker",
    )
    assert upload_claims["sub"] == str(eligible_resource_target_id)
    assert upload_claims["bootstrap_job_id"] == str(job_id)
    assert upload_claims["k3s_server_url"] == "https://192.0.2.10:6443"

    kubeconfig, payload = _kubeconfig("https://192.0.2.10:6443")
    payload["bootstrap_job_id"] = str(job_id)
    payload["resource_target_id"] = str(eligible_resource_target_id)
    uploaded = credential_client.post(
        f"/v1/agent/bootstrap-jobs/{job_id}/k3s-credentials",
        headers={"Authorization": f"Bearer {upload_token}"},
        json=payload,
    )
    assert uploaded.status_code == 201

    db_session.expire_all()
    stored = db_session.get(K3sCredentialTable, eligible_resource_target_id)
    assert stored is not None
    assert stored.source_job_id == job_id
    assert kubeconfig.encode("utf-8") not in stored.encrypted_kubeconfig
    assert stored.encryption_scheme == "fernet-v1"

    invalid_pull = credential_client.get(
        "/v1/runtime/k3s-credentials",
        headers={"Authorization": f"Bearer {_runtime_token('x' * 40)}"},
    )
    assert invalid_pull.status_code == 401
    assert invalid_pull.json()["error"]["code"] == "INVALID_RUNTIME_TOKEN"

    runtime_token = _runtime_token()
    pulled = credential_client.get(
        "/v1/runtime/k3s-credentials?limit=100",
        headers={"Authorization": f"Bearer {runtime_token}"},
    )
    assert pulled.status_code == 200
    response = pulled.json()
    assert response["next_cursor"] is None
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["resource_target_id"] == str(eligible_resource_target_id)
    assert item["source_bootstrap_job_id"] == str(job_id)
    assert base64.b64decode(item["kubeconfig_base64"]).decode() == kubeconfig

    single = credential_client.get(
        f"/v1/runtime/k3s-credentials/{eligible_resource_target_id}",
        headers={"Authorization": f"Bearer {_runtime_token()}"},
    )
    assert single.status_code == 200
    assert single.json()["kubeconfig_base64"] == item["kubeconfig_base64"]
