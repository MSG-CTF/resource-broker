import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from app.adapters.azure_windows_reset import (
    AzureWindowsResetAdapter,
    AzureWindowsResetError,
    AzureWindowsResetTarget,
    source_resource_parts,
    vm_resource_parts,
)
from app.db.session import get_session_factory
from app.repositories.windows_runtime import WindowsRuntimeRepository


LOGGER = logging.getLogger(__name__)
_retry_after: dict[UUID, float] = {}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _configuration() -> tuple[str, dict[UUID, str]]:
    source_id = os.getenv("WINDOWS_RESET_SOURCE_ID", "").strip()
    raw_targets = os.getenv("WINDOWS_RESET_TARGETS_JSON", "").strip()
    if not source_id and not raw_targets:
        return "", {}
    if not source_id or not raw_targets:
        raise ValueError("Both Windows reset settings are required.")
    source_resource_parts(source_id)
    targets = json.loads(raw_targets)
    if not isinstance(targets, dict) or not targets:
        raise ValueError("Windows reset targets must be a nonempty object.")
    result = {}
    for key, vm_id in targets.items():
        if not isinstance(key, str) or not isinstance(vm_id, str):
            raise ValueError("Each Windows reset target needs a Broker UUID and Azure VM ID.")
        target_id = UUID(key)
        if target_id.int == 0 or target_id in result:
            raise ValueError("Windows reset target IDs must be unique nonzero UUIDs.")
        vm_resource_parts(vm_id)
        result[target_id] = vm_id
    return source_id, result


def _source_for_target(resource_target_id: UUID, default_source: str, targets: dict[UUID, str]) -> str:
    raw = os.getenv("WINDOWS_RESET_SOURCE_IDS_JSON", "").strip()
    if not raw:
        return default_source
    values = json.loads(raw)
    if not isinstance(values, dict):
        raise ValueError("Windows reset source overrides must be an object.")
    sources = {}
    for key, source_id in values.items():
        if not isinstance(key, str) or not isinstance(source_id, str):
            raise ValueError("Each Windows reset source override needs a Broker UUID and Azure image ID.")
        target_id = UUID(key)
        if target_id not in targets or target_id in sources:
            raise ValueError("Windows reset source override target is not configured or is duplicated.")
        source_resource_parts(source_id)
        sources[target_id] = source_id
    return sources.get(resource_target_id, default_source)


def process_one() -> bool:
    source_id, targets = _configuration()
    if not targets:
        return False
    now = monotonic()
    eligible = tuple(target_id for target_id in targets if _retry_after.get(target_id, 0) <= now)
    with get_session_factory()() as session:
        repository = WindowsRuntimeRepository(session)
        row = repository.pending_reset(eligible, finished_before=datetime.now(UTC) - timedelta(seconds=30))
        if row is None:
            return False
        target, account, connection, execution = row
        resource_target_id = target.resource_target_id
        target_source_id = _source_for_target(resource_target_id, source_id, targets)
        config = account.provider_config or {}
        try:
            # shortcut: hold one VM row lock through Azure calls; use durable leases for high reset concurrency.
            if account.auth_method != "GOOGLE_OIDC_CLIENT_ASSERTION" or not isinstance(config.get("client_id"), str):
                raise AzureWindowsResetError("federated account configuration")
            if target.provider_scope_id is None or target.provider_instance_id is None or connection.current_execution_id is None:
                raise AzureWindowsResetError("Broker VM identity")
            subscription_id, _, _ = vm_resource_parts(targets[resource_target_id])
            if subscription_id.lower() != target.provider_scope_id.lower():
                raise AzureWindowsResetError("Broker VM subscription")
            source_subscription_id = source_resource_parts(target_source_id)[1]
            source_account = None
            source_config = {}
            if source_subscription_id.lower() != subscription_id.lower():
                source_account = repository.azure_account_for_subscription(source_subscription_id)
                source_config = (source_account.provider_config or {}) if source_account else {}
                if (
                    source_account is None or not source_account.enabled
                    or source_account.auth_method != "GOOGLE_OIDC_CLIENT_ASSERTION"
                    or not isinstance(source_config.get("client_id"), str)
                ):
                    raise AzureWindowsResetError("source account")
            adapter_target = AzureWindowsResetTarget(
                tenant_id=account.external_account_id,
                client_id=config["client_id"],
                subscription_id=subscription_id,
                instance_id=target.provider_instance_id,
                source_id=target_source_id,
                expected_vm_id=targets[resource_target_id],
                execution_id=connection.current_execution_id,
                source_tenant_id=source_account.external_account_id if source_account else None,
                source_client_id=source_config.get("client_id") if source_account else None,
            )
            executor_certificate_pem, public_ip = AzureWindowsResetAdapter.from_google_oidc(adapter_target).restore()
        except AzureWindowsResetError as error:
            _retry_after[resource_target_id] = monotonic() + 60
            LOGGER.warning("Windows reset for %s stopped at %s", resource_target_id, error.stage)
            return False
        connection.state = "READY"
        connection.current_execution_id = None
        connection.executor_certificate_pem = executor_certificate_pem
        connection.updated_at = datetime.now(UTC)
        execution.recovery_event_id = execution.recovery_event_id or uuid4()
        target.provider_instance_state = "RUNNING"
        target.public_ip = public_ip
        session.commit()
        _retry_after.pop(resource_target_id, None)
        LOGGER.info("Windows reset completed for %s", resource_target_id)
        return True


def _recovery_configuration() -> tuple[str, str]:
    url = os.getenv("WINDOWS_RECOVERY_RUNTIME_URL", "").strip().rstrip("/")
    token = os.getenv("WINDOWS_RECOVERY_TOKEN", "")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise ValueError("Windows recovery Runtime URL is invalid") from error
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path
        or parts.query
        or parts.fragment
        or port == 0
        or url != f"{parts.scheme}://{parts.netloc}"
        or not token
        or any(not 33 <= ord(char) <= 126 for char in token)
    ):
        raise ValueError("Windows recovery Runtime URL and token must be configured")
    return url, token


def deliver_recovery_one() -> bool:
    with get_session_factory()() as session:
        execution = WindowsRuntimeRepository(session).pending_recovery(datetime.now(UTC))
        if execution is None:
            return False
        try:
            url, token = _recovery_configuration()
            body = json.dumps({
                "execution_id": str(execution.execution_id),
                "event_id": str(execution.recovery_event_id),
                "vm_state": "READY",
            }).encode()
            request = Request(
                f"{url}/internal/v1/windows-vms/{execution.resource_target_id}/recovery-events",
                data=body,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                method="POST",
            )
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise ValueError("Windows recovery Runtime response was not successful")
        except HTTPError as error:
            LOGGER.warning("Windows recovery notification for %s returned HTTP %s", execution.execution_id, error.code)
        except (OSError, ValueError) as error:
            LOGGER.warning("Windows recovery notification for %s failed (%s)", execution.execution_id, type(error).__name__)
        else:
            execution.recovery_delivered_at = datetime.now(UTC)
            execution.recovery_retry_at = None
            session.commit()
            LOGGER.info("Windows recovery notification accepted for %s", execution.execution_id)
            return True
        execution.recovery_retry_at = datetime.now(UTC) + timedelta(seconds=30)
        session.commit()
        return False


async def run_windows_reset_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(process_one)
        except Exception:
            LOGGER.exception("Windows reset worker iteration failed")
            processed = False
        try:
            delivered = await asyncio.to_thread(deliver_recovery_one)
        except Exception:
            LOGGER.exception("Windows recovery notification iteration failed")
            delivered = False
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=1 if processed or delivered else 10)
