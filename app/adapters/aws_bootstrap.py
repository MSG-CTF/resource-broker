from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import shlex
from typing import Any, Protocol

from app.adapters.aws import (
    AwsAdapterError,
    AwsDependencyError,
    AwsFederation,
    _client_error_code,
    _translate_aws_error,
)


class AwsSsmClient(Protocol):
    def send_command(self, **kwargs: object) -> Mapping[str, Any]:
        raise NotImplementedError

    def get_command_invocation(self, **kwargs: object) -> Mapping[str, Any]:
        raise NotImplementedError

    def cancel_command(self, **kwargs: object) -> Mapping[str, Any]:
        raise NotImplementedError


class AwsBootstrapError(AwsAdapterError):
    error_code = "AWS_BOOTSTRAP_ERROR"
    public_message = "The AWS bootstrap command could not be managed."


class AwsBootstrapInvalidResponseError(AwsBootstrapError):
    error_code = "AWS_SSM_INVALID_RESPONSE"
    public_message = "AWS Systems Manager returned an invalid command response."


class AwsBootstrapTargetNotManagedError(AwsBootstrapError):
    error_code = "AWS_SSM_TARGET_NOT_MANAGED"
    public_message = (
        "The EC2 instance is not an online Linux Systems Manager managed node."
    )


@dataclass(frozen=True, slots=True)
class AwsBootstrapTarget:
    account_id: str
    region: str
    instance_id: str
    role_arn: str
    external_id: str


@dataclass(frozen=True, slots=True)
class AwsBootstrapCommandStatus:
    complete: bool
    succeeded: bool | None = None
    error_code: str | None = None
    error_message: str | None = None


_COMMAND_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_PENDING_STATUSES = frozenset({"Pending", "InProgress", "Delayed", "Cancelling"})
_SUCCESS_STATUSES = frozenset({"Success"})
_FAILED_STATUSES = frozenset({"Cancelled", "TimedOut", "Failed"})


def _translate_bootstrap_error(scope_id: str, error: Exception) -> AwsAdapterError:
    if _client_error_code(error) in {
        "InvalidInstanceId",
        "TargetNotConnected",
        "UnsupportedPlatformType",
    }:
        return AwsBootstrapTargetNotManagedError(scope_id)
    return _translate_aws_error(scope_id, error)


def _runner_download_command(runner_url: str, runner_sha256: str) -> str:
    url = shlex.quote(runner_url)
    digest = shlex.quote(runner_sha256)
    return f"""set -eu
command -v curl >/dev/null 2>&1 || {{
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends ca-certificates curl
}}
work_dir="$(mktemp -d)"
cleanup() {{ rm -rf -- "${{work_dir}}"; }}
trap cleanup EXIT
curl --fail --silent --show-error --location --retry 3 \
  --proto '=https' --tlsv1.2 {url} --output "${{work_dir}}/runner.sh"
printf '%s  %s\n' {digest} "${{work_dir}}/runner.sh" \
  | sha256sum --check --strict
chmod 0700 "${{work_dir}}/runner.sh"
bash "${{work_dir}}/runner.sh"
"""


class AwsBootstrapAdapter:
    def __init__(self, target: AwsBootstrapTarget, ssm_client: AwsSsmClient) -> None:
        self._target = target
        self._ssm = ssm_client

    @classmethod
    def from_google_oidc(
        cls,
        target: AwsBootstrapTarget,
        hub_role_arn: str,
        audience: str,
        sts_region: str,
    ) -> "AwsBootstrapAdapter":
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as error:
            raise AwsDependencyError(target.region) from error
        federation = AwsFederation.from_google_oidc(
            hub_role_arn,
            audience,
            sts_region,
        )
        credentials = federation.assume_spoke_role(
            expected_account_id=target.account_id,
            spoke_role_arn=target.role_arn,
            external_id=target.external_id,
        )
        ssm = boto3.client(
            "ssm",
            region_name=target.region,
            **credentials.client_kwargs(),
            config=Config(retries={"mode": "standard", "max_attempts": 5}),
        )
        return cls(target, ssm)

    def send_runner(
        self,
        *,
        runner_url: str,
        runner_sha256: str,
        job_id: str,
        timeout_seconds: int,
    ) -> str:
        command = _runner_download_command(runner_url, runner_sha256)
        try:
            response = self._ssm.send_command(
                DocumentName="AWS-RunShellScript",
                InstanceIds=[self._target.instance_id],
                Comment=f"MSG Broker bootstrap job {job_id}",
                Parameters={
                    "commands": [command],
                    "executionTimeout": [str(timeout_seconds)],
                },
                TimeoutSeconds=min(max(timeout_seconds, 30), 2592000),
                CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
            )
        except Exception as error:
            raise _translate_bootstrap_error(self._target.region, error) from error
        command_data = response.get("Command") if isinstance(response, Mapping) else None
        command_id = (
            command_data.get("CommandId")
            if isinstance(command_data, Mapping)
            else None
        )
        if not isinstance(command_id, str) or _COMMAND_ID.fullmatch(command_id) is None:
            raise AwsBootstrapInvalidResponseError(self._target.region)
        return command_id

    def command_status(self, command_id: str) -> AwsBootstrapCommandStatus:
        try:
            response = self._ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=self._target.instance_id,
            )
        except Exception as error:
            if _client_error_code(error) == "InvocationDoesNotExist":
                return AwsBootstrapCommandStatus(complete=False)
            raise _translate_bootstrap_error(self._target.region, error) from error
        status = response.get("Status") if isinstance(response, Mapping) else None
        if status in _PENDING_STATUSES:
            return AwsBootstrapCommandStatus(complete=False)
        if status in _SUCCESS_STATUSES:
            return AwsBootstrapCommandStatus(complete=True, succeeded=True)
        if status in _FAILED_STATUSES:
            normalized = str(status).upper().replace(" ", "_")
            return AwsBootstrapCommandStatus(
                complete=True,
                succeeded=False,
                error_code=f"AWS_SSM_COMMAND_{normalized}",
                error_message=(
                    f"AWS Systems Manager reported command status {status}."
                ),
            )
        raise AwsBootstrapInvalidResponseError(self._target.region)

    def cancel_command(self, command_id: str) -> None:
        try:
            self._ssm.cancel_command(
                CommandId=command_id,
                InstanceIds=[self._target.instance_id],
            )
        except Exception as error:
            if _client_error_code(error) in {
                "InvalidCommandId",
                "InvocationDoesNotExist",
            }:
                return
            raise _translate_bootstrap_error(self._target.region, error) from error
