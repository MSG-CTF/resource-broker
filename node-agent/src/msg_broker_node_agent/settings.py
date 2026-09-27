from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


class AgentSettingsError(ValueError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AgentSettingsError(f"{name} must be configured")
    return value


def _positive_int(name: str, default: int, *, minimum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise AgentSettingsError(f"{name} must be an integer") from error
    if value < minimum:
        raise AgentSettingsError(f"{name} must be at least {minimum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AgentSettingsError(f"{name} must be true or false")


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


@dataclass(frozen=True, slots=True)
class AgentSettings:
    node_name: str
    broker_observations_url: str | None
    interval_seconds: int
    initial_jitter_seconds: int
    request_timeout_seconds: int
    resource_target_annotation: str
    resource_target_id_override: str | None
    dry_run: bool
    allow_insecure_http: bool
    broker_ca_file: Path | None
    broker_client_cert_file: Path | None
    broker_client_key_file: Path | None

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        dry_run = _boolean("AGENT_DRY_RUN", False)
        broker_url = os.getenv("BROKER_OBSERVATIONS_URL", "").strip() or None
        if not dry_run and broker_url is None:
            raise AgentSettingsError(
                "BROKER_OBSERVATIONS_URL must be configured when dry-run is disabled"
            )

        allow_insecure_http = _boolean("ALLOW_INSECURE_HTTP", False)
        if broker_url is not None:
            scheme = urlparse(broker_url).scheme.lower()
            if scheme not in {"http", "https"}:
                raise AgentSettingsError(
                    "BROKER_OBSERVATIONS_URL must use http or https"
                )
            if scheme != "https" and not allow_insecure_http:
                raise AgentSettingsError(
                    "BROKER_OBSERVATIONS_URL must use https unless "
                    "ALLOW_INSECURE_HTTP is enabled"
                )

        client_cert = _optional_path("BROKER_CLIENT_CERT_FILE")
        client_key = _optional_path("BROKER_CLIENT_KEY_FILE")
        if (client_cert is None) != (client_key is None):
            raise AgentSettingsError(
                "BROKER_CLIENT_CERT_FILE and BROKER_CLIENT_KEY_FILE must be "
                "configured together"
            )

        interval_seconds = _positive_int(
            "AGENT_INTERVAL_SECONDS",
            300,
            minimum=10,
        )
        initial_jitter_seconds = _positive_int(
            "AGENT_INITIAL_JITTER_SECONDS",
            30,
            minimum=0,
        )
        if initial_jitter_seconds > interval_seconds:
            raise AgentSettingsError(
                "AGENT_INITIAL_JITTER_SECONDS must not exceed "
                "AGENT_INTERVAL_SECONDS"
            )

        return cls(
            node_name=_required("NODE_NAME"),
            broker_observations_url=broker_url,
            interval_seconds=interval_seconds,
            initial_jitter_seconds=initial_jitter_seconds,
            request_timeout_seconds=_positive_int(
                "BROKER_REQUEST_TIMEOUT_SECONDS",
                15,
                minimum=1,
            ),
            resource_target_annotation=(
                os.getenv(
                    "RESOURCE_TARGET_ANNOTATION",
                    "msg-broker.io/resource-target-id",
                ).strip()
                or "msg-broker.io/resource-target-id"
            ),
            resource_target_id_override=(
                os.getenv("RESOURCE_TARGET_ID", "").strip() or None
            ),
            dry_run=dry_run,
            allow_insecure_http=allow_insecure_http,
            broker_ca_file=_optional_path("BROKER_CA_FILE"),
            broker_client_cert_file=client_cert,
            broker_client_key_file=client_key,
        )
