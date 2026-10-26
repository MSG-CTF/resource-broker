import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from time import time
from typing import Any
from uuid import UUID

from app.adapters.azure import AzureAdapter, AzureUserLoginRequiredError


ARM_SCOPE = "https://management.azure.com/.default"
_lock = Lock()
# shortcut: one API process keeps short-lived access tokens in memory;
# reconnect after expiry/restart, or add a shared credential store for replicas.
_sessions: dict[UUID, "_UserCredential"] = {}


class AzureBrowserLoginNotConfiguredError(RuntimeError):
    pass


def _client_id() -> str | None:
    value = os.getenv("AZURE_USER_LOGIN_CLIENT_ID", "").strip()
    try:
        return str(UUID(value))
    except ValueError:
        return None


@dataclass
class _UserCredential:
    tenant_id: str
    access_token: str = field(repr=False)
    expires_on: int

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        if scopes != (ARM_SCOPE,) or self.expires_on <= time() + 30:
            raise AzureUserLoginRequiredError(self.tenant_id)
        from azure.core.credentials import AccessToken

        return AccessToken(self.access_token, self.expires_on)

    def close(self) -> None:
        self.access_token = ""


def login_status(account_id: UUID, tenant_id: str) -> dict[str, Any]:
    client_id = _client_id()
    with _lock:
        session = _sessions.get(account_id)
        if session is not None and session.expires_on <= time() + 30:
            _sessions.pop(account_id).close()
            session = None
        return {
            "status": "CONNECTED" if session is not None else "DISCONNECTED",
            "client_id": client_id,
            "tenant_id": tenant_id,
            "expires_at": (
                datetime.fromtimestamp(session.expires_on, UTC) if session else None
            ),
            "message": None if client_id else "중앙 브로커용 Microsoft 앱 등록 설정이 필요합니다.",
        }


def connect(
    account_id: UUID,
    tenant_id: str,
    subscription_ids: tuple[str, ...],
    access_token: str,
    expires_on: int,
) -> dict[str, Any]:
    if _client_id() is None:
        raise AzureBrowserLoginNotConfiguredError
    if (not access_token or access_token != access_token.strip()
            or len(access_token) > 16384 or expires_on <= time() + 60):
        raise ValueError("Invalid or expired Azure access token.")
    credential = _UserCredential(
        tenant_id, access_token, min(expires_on, int(time()) + 3600)
    )
    # ARM validates the opaque token and configured subscription permissions.
    # This credential never authenticates a caller to Broker itself.
    try:
        for subscription_id in subscription_ids:
            AzureAdapter.from_user_credential(subscription_id, credential).check_user_access()
    except Exception:
        credential.close()
        raise
    with _lock:
        previous = _sessions.get(account_id)
        _sessions[account_id] = credential
    if previous is not None:
        previous.close()
    return login_status(account_id, tenant_id)


def user_credential(account_id: UUID, tenant_id: str) -> Any:
    with _lock:
        credential = _sessions.get(account_id)
        if (credential is not None and credential.tenant_id == tenant_id
                and credential.expires_on > time() + 30):
            return credential
    raise AzureUserLoginRequiredError(tenant_id)


def disconnect(account_id: UUID) -> None:
    with _lock:
        credential = _sessions.pop(account_id, None)
    if credential is not None:
        credential.close()
