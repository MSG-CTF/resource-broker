from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shlex
from uuid import UUID

from app.domain.enums import BootstrapAction, Provider
from app.services.azure_instance_identity import azure_attestation_nonce


class BootstrapArtifactNotFoundError(LookupError):
    pass


class BootstrapConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapArtifact:
    path: Path
    sha256: str
    public_url: str


def _public_base_url() -> str:
    value = os.getenv(
        "BOOTSTRAP_PUBLIC_BASE_URL",
        "https://agents.mjsec.kr",
    ).strip().rstrip("/")
    if not value.startswith("https://"):
        raise BootstrapConfigurationError(
            "BOOTSTRAP_PUBLIC_BASE_URL must use HTTPS."
        )
    return value


def _artifact_directory() -> Path:
    value = os.getenv(
        "BOOTSTRAP_ARTIFACT_DIR",
        "/srv/broker/bootstrap-artifacts",
    ).strip()
    path = Path(value)
    if not path.is_absolute():
        raise BootstrapConfigurationError(
            "BOOTSTRAP_ARTIFACT_DIR must be absolute."
        )
    return path


def artifact_for_version(version: str) -> BootstrapArtifact:
    filename = f"msg-broker-node-agent-bootstrap-{version}.tar.gz"
    path = _artifact_directory() / filename
    if not path.is_file():
        raise BootstrapArtifactNotFoundError(version)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return BootstrapArtifact(
        path=path,
        sha256=digest,
        public_url=(
            f"{_public_base_url()}/v1/agent/bootstrap-artifacts/{filename}"
        ),
    )


def enrollment_audience(job_id: UUID) -> str:
    return (
        f"{_public_base_url()}/v1/agent/bootstrap-enrollments/{job_id}"
    )


def runner_public_url(job_id: UUID) -> str:
    return f"{_public_base_url()}/v1/agent/bootstrap-jobs/{job_id}/runner.sh"


def render_runner_script(
    *,
    job_id: UUID,
    resource_target_id: UUID,
    provider: Provider = Provider.GCP,
    action: BootstrapAction,
    bootstrap_version: str,
    artifact_url: str,
    artifact_sha256: str,
    enrollment_url: str,
    enrollment_audience_value: str,
    k3s_version: str | None,
    agent_image: str | None,
) -> str:
    enrollment_mode = {
        Provider.GCP: "gcp-identity",
        Provider.AWS: "aws-instance-identity",
        Provider.AZURE: "azure-attested-identity",
    }.get(provider)
    if enrollment_mode is None:
        raise BootstrapConfigurationError(
            f"Bootstrap runner is not implemented for {provider.value}."
        )
    success_exit_code = "100" if provider is Provider.GCP else "0"
    values = {
        "JOB_ID": str(job_id),
        "RESOURCE_TARGET_ID": str(resource_target_id),
        "ACTION": action.value.lower(),
        "BOOTSTRAP_VERSION": bootstrap_version,
        "ARTIFACT_URL": artifact_url,
        "ARTIFACT_SHA256": artifact_sha256,
        "ENROLLMENT_URL": enrollment_url,
        "ENROLLMENT_AUDIENCE": enrollment_audience_value,
        "AZURE_ATTESTATION_NONCE": (
            azure_attestation_nonce(job_id)
            if provider is Provider.AZURE
            else ""
        ),
        "K3S_VERSION": k3s_version or "",
        "AGENT_IMAGE": agent_image or "",
    }
    assignments = "\n".join(
        f"{name}={shlex.quote(value)}" for name, value in values.items()
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
{assignments}
MARKER_DIR=/var/lib/msg-broker-bootstrap/jobs
WORK_DIR="$(mktemp -d)"
cleanup() {{ rm -rf -- "${{WORK_DIR}}"; }}
trap cleanup EXIT

command -v curl >/dev/null 2>&1 || {{
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends ca-certificates curl
}}
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "${{ARTIFACT_URL}}" --output "${{WORK_DIR}}/bootstrap.tar.gz"
printf '%s  %s\n' "${{ARTIFACT_SHA256}}" "${{WORK_DIR}}/bootstrap.tar.gz" \
  | sha256sum --check --strict
tar -xzf "${{WORK_DIR}}/bootstrap.tar.gz" -C "${{WORK_DIR}}"
SCRIPT="${{WORK_DIR}}/msg-broker-node-agent-bootstrap-${{BOOTSTRAP_VERSION}}/deploy/bootstrap/node-agent-bootstrap.sh"
test -x "${{SCRIPT}}"
MSG_BROKER_RESOURCE_TARGET_ID="${{RESOURCE_TARGET_ID}}" \
MSG_BROKER_K3S_VERSION="${{K3S_VERSION}}" \
MSG_BROKER_AGENT_IMAGE="${{AGENT_IMAGE}}" \
MSG_BROKER_ENROLLMENT_MODE={enrollment_mode} \
MSG_BROKER_ENROLLMENT_URL="${{ENROLLMENT_URL}}" \
MSG_BROKER_ENROLLMENT_AUDIENCE="${{ENROLLMENT_AUDIENCE}}" \
MSG_BROKER_AZURE_ATTESTATION_NONCE="${{AZURE_ATTESTATION_NONCE}}" \
  bash "${{SCRIPT}}" "${{ACTION}}"
install -d -o root -g root -m 0700 "${{MARKER_DIR}}"
printf '%s\n' "${{ACTION}}" > "${{MARKER_DIR}}/${{JOB_ID}}.done"
chmod 0600 "${{MARKER_DIR}}/${{JOB_ID}}.done"
exit {success_exit_code}
"""


def runner_sha256(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()
