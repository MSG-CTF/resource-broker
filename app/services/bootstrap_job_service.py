from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import os
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.aws import AwsAdapterError
from app.adapters.aws_bootstrap import AwsBootstrapAdapter, AwsBootstrapTarget
from app.adapters.gcp import GcpAdapterError
from app.adapters.gcp_bootstrap import (
    GcpBootstrapAdapter,
    GcpBootstrapConflictError,
    GcpBootstrapTarget,
)
from app.db.tables import BootstrapJobTable, ResourceTargetTable
from app.domain.enums import (
    Architecture,
    BootstrapAction,
    BootstrapJobStatus,
    Provider,
)
from app.repositories.bootstrap_jobs import BootstrapJobRepository
from app.services.bootstrap_artifacts import (
    artifact_for_version,
    enrollment_audience,
    render_runner_script,
    runner_public_url,
    runner_sha256,
)


_GCP_LABEL_KEY = "msg-broker-bootstrap-job"
_AWS_OPT_IN_TAG_KEY = "msg-broker-bootstrap"
_AWS_OPT_IN_TAG_VALUE = "enabled"
_SUPPORTED_BOOTSTRAP_ARCHITECTURES = frozenset(
    {Architecture.AMD64, Architecture.ARM64}
)


class BootstrapResourceTargetNotFoundError(LookupError):
    pass


class BootstrapResourceTargetInvalidError(ValueError):
    pass


class BootstrapJobConflictError(ValueError):
    pass


class BootstrapProviderNotImplementedError(NotImplementedError):
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        super().__init__(provider.value)


GcpBootstrapAdapterFactory = Callable[[GcpBootstrapTarget], GcpBootstrapAdapter]
AwsBootstrapAdapterFactory = Callable[
    [AwsBootstrapTarget, str, str, str], AwsBootstrapAdapter
]
AwsSettingProvider = Callable[[], str | None]


def _aws_hub_role_arn_from_environment() -> str | None:
    return os.getenv("AWS_FEDERATION_HUB_ROLE_ARN")


def _aws_federation_audience_from_environment() -> str | None:
    return os.getenv("AWS_FEDERATION_AUDIENCE")


def _aws_sts_region_from_environment() -> str | None:
    return os.getenv("AWS_STS_REGION", "us-east-1")


def _timeout_seconds() -> int:
    raw = os.getenv("BOOTSTRAP_JOB_TIMEOUT_SECONDS", "7200").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("BOOTSTRAP_JOB_TIMEOUT_SECONDS must be an integer") from error
    if value < 300 or value > 7200:
        raise RuntimeError(
            "BOOTSTRAP_JOB_TIMEOUT_SECONDS must be between 300 and 7200"
        )
    return value


def _max_active_assignments_per_zone() -> int:
    raw = os.getenv(
        "BOOTSTRAP_GCP_MAX_ACTIVE_ASSIGNMENTS_PER_ZONE",
        "10",
    ).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(
            "BOOTSTRAP_GCP_MAX_ACTIVE_ASSIGNMENTS_PER_ZONE must be an integer"
        ) from error
    if value < 1 or value > 19:
        raise RuntimeError(
            "BOOTSTRAP_GCP_MAX_ACTIVE_ASSIGNMENTS_PER_ZONE must be between 1 and 19"
        )
    return value


def _assignment_id(job_id: UUID) -> str:
    return f"msg-bootstrap-{job_id.hex[:16]}"


def _label_value(job_id: UUID) -> str:
    return f"job-{job_id.hex[:20]}"


def _runner(job: BootstrapJobTable) -> str:
    artifact = artifact_for_version(job.bootstrap_version)
    return render_runner_script(
        job_id=job.job_id,
        resource_target_id=job.resource_target_id,
        provider=job.provider,
        action=job.action,
        bootstrap_version=job.bootstrap_version,
        artifact_url=artifact.public_url,
        artifact_sha256=job.artifact_sha256,
        enrollment_url=job.enrollment_audience,
        enrollment_audience_value=job.enrollment_audience,
        k3s_version=job.k3s_version,
        agent_image=job.agent_image,
    )


def render_job_runner(job: BootstrapJobTable) -> str:
    script = _runner(job)
    if runner_sha256(script) != job.runner_sha256:
        raise RuntimeError("Stored runner checksum does not match job input")
    return script


class BootstrapJobService:
    def __init__(
        self,
        session: Session,
        gcp_adapter_factory: GcpBootstrapAdapterFactory = GcpBootstrapAdapter.from_adc,
        aws_adapter_factory: AwsBootstrapAdapterFactory = (
            AwsBootstrapAdapter.from_google_oidc
        ),
        aws_hub_role_arn_provider: AwsSettingProvider = (
            _aws_hub_role_arn_from_environment
        ),
        aws_federation_audience_provider: AwsSettingProvider = (
            _aws_federation_audience_from_environment
        ),
        aws_sts_region_provider: AwsSettingProvider = _aws_sts_region_from_environment,
    ) -> None:
        self._session = session
        self._jobs = BootstrapJobRepository(session)
        self._gcp_adapter_factory = gcp_adapter_factory
        self._aws_adapter_factory = aws_adapter_factory
        self._aws_hub_role_arn_provider = aws_hub_role_arn_provider
        self._aws_federation_audience_provider = aws_federation_audience_provider
        self._aws_sts_region_provider = aws_sts_region_provider

    def create(
        self,
        *,
        resource_target_id: UUID,
        action: BootstrapAction,
        bootstrap_version: str,
        k3s_version: str | None,
        agent_image: str | None,
    ) -> BootstrapJobTable:
        target = self._jobs.get_resource_target(resource_target_id)
        if target is None:
            raise BootstrapResourceTargetNotFoundError
        if not self._jobs.lock_provider_account(target.account_id):
            raise BootstrapResourceTargetNotFoundError
        target = self._jobs.get_resource_target(resource_target_id)
        if target is None:
            raise BootstrapResourceTargetNotFoundError
        provider = target.account.provider
        if provider not in {Provider.GCP, Provider.AWS}:
            raise BootstrapProviderNotImplementedError(provider)
        self._validate_target_for_create(target, provider)
        if self._jobs.active_for_resource(resource_target_id) is not None:
            raise BootstrapJobConflictError

        artifact = artifact_for_version(bootstrap_version)
        now = datetime.now(UTC)
        job_id = uuid4()
        is_gcp = provider is Provider.GCP
        job = BootstrapJobTable(
            job_id=job_id,
            resource_target_id=resource_target_id,
            provider=provider,
            action=action,
            status=BootstrapJobStatus.QUEUED,
            bootstrap_version=bootstrap_version,
            k3s_version=k3s_version,
            agent_image=agent_image,
            artifact_sha256=artifact.sha256,
            runner_sha256="0" * 64,
            enrollment_audience=enrollment_audience(job_id),
            provider_job_id=_assignment_id(job_id) if is_gcp else None,
            temporary_label_key=_GCP_LABEL_KEY if is_gcp else _AWS_OPT_IN_TAG_KEY,
            temporary_label_value=(
                _label_value(job_id) if is_gcp else _AWS_OPT_IN_TAG_VALUE
            ),
            deadline_at=now + timedelta(seconds=_timeout_seconds()),
            created_at=now,
            updated_at=now,
        )
        job.runner_sha256 = runner_sha256(_runner(job))
        try:
            self._jobs.add(job)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise BootstrapJobConflictError from error
        return job

    def get(self, job_id: UUID) -> BootstrapJobTable:
        job = self._jobs.get(job_id)
        if job is None:
            raise BootstrapResourceTargetNotFoundError
        return job

    def list_for_resource(
        self,
        resource_target_id: UUID,
    ) -> tuple[BootstrapJobTable, ...]:
        if self._jobs.get_resource_target(resource_target_id) is None:
            raise BootstrapResourceTargetNotFoundError
        return self._jobs.list_for_resource(resource_target_id)

    def process_next(self) -> bool:
        job = self._jobs.next_active()
        if job is None:
            self._session.rollback()
            return False
        job_id = job.job_id
        if job.status is BootstrapJobStatus.QUEUED:
            resource = self._jobs.get_resource_target(job.resource_target_id)
            if (
                job.provider is Provider.GCP
                and resource is not None
                and resource.provider_scope_id is not None
                and resource.zone is not None
                and self._jobs.active_assignment_count(
                    resource.provider_scope_id,
                    resource.zone,
                )
                >= _max_active_assignments_per_zone()
            ):
                job.updated_at = datetime.now(UTC)
                self._session.commit()
                return True
            now = datetime.now(UTC)
            job.status = BootstrapJobStatus.APPLYING
            job.started_at = now
            job.deadline_at = now + timedelta(seconds=_timeout_seconds())
            job.error_code = None
            job.error_message = None
            self._session.commit()
        else:
            self._session.commit()
        self._process(job_id)
        return True

    @staticmethod
    def _validate_target_for_create(
        resource: ResourceTargetTable,
        provider: Provider,
    ) -> None:
        if resource.retired_at is not None or resource.instance_name is None:
            raise BootstrapResourceTargetInvalidError
        if resource.architecture not in _SUPPORTED_BOOTSTRAP_ARCHITECTURES:
            raise BootstrapResourceTargetInvalidError
        if provider is Provider.GCP:
            if resource.provider_scope_id is None or resource.zone is None:
                raise BootstrapResourceTargetInvalidError
        elif provider is Provider.AWS:
            if resource.provider_scope_id is None:
                raise BootstrapResourceTargetInvalidError

    @staticmethod
    def _gcp_target(resource: ResourceTargetTable) -> GcpBootstrapTarget:
        if (
            resource.provider_scope_id is None
            or resource.zone is None
            or resource.instance_name is None
        ):
            raise BootstrapResourceTargetInvalidError
        return GcpBootstrapTarget(
            project_id=resource.provider_scope_id,
            zone=resource.zone,
            instance_name=resource.instance_name,
            instance_id=resource.provider_instance_id,
        )

    @staticmethod
    def _aws_target(resource: ResourceTargetTable) -> AwsBootstrapTarget:
        if resource.provider_scope_id is None:
            raise BootstrapResourceTargetInvalidError
        account = resource.account
        expected_prefix = f"arn:aws:iam::{account.external_account_id}:role/"
        role_arn = account.provider_config.get("bootstrap_role_arn")
        if not isinstance(role_arn, str) or not role_arn.startswith(expected_prefix):
            role_arn = expected_prefix + "MsgBrokerBootstrapRole"
        external_id = account.provider_config.get("external_id")
        if not isinstance(external_id, str) or not external_id:
            external_id = f"mbe-{account.account_id}"
        return AwsBootstrapTarget(
            account_id=account.external_account_id,
            region=resource.provider_scope_id,
            instance_id=resource.provider_instance_id,
            role_arn=role_arn,
            external_id=external_id,
        )

    @staticmethod
    def _required_aws_setting(provider: AwsSettingProvider, message: str) -> str:
        value = provider()
        if not isinstance(value, str) or not value or value.strip() != value:
            raise RuntimeError(message)
        return value

    def _aws_adapter(self, resource: ResourceTargetTable) -> AwsBootstrapAdapter:
        return self._aws_adapter_factory(
            self._aws_target(resource),
            self._required_aws_setting(
                self._aws_hub_role_arn_provider,
                "The AWS federation Hub Role ARN is not configured.",
            ),
            self._required_aws_setting(
                self._aws_federation_audience_provider,
                "The AWS federation audience is not configured.",
            ),
            self._required_aws_setting(
                self._aws_sts_region_provider,
                "The AWS STS Region is not configured.",
            ),
        )

    def _process(self, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        resource = self._jobs.get_resource_target(job.resource_target_id)
        if resource is None:
            return
        if job.provider is Provider.GCP:
            self._process_gcp(job, resource)
        elif job.provider is Provider.AWS:
            self._process_aws(job, resource)
        else:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "PROVIDER_BOOTSTRAP_NOT_IMPLEMENTED",
                "Bootstrap is not implemented for this provider.",
            )

    def _process_gcp(
        self,
        job: BootstrapJobTable,
        resource: ResourceTargetTable,
    ) -> None:
        try:
            adapter = self._gcp_adapter_factory(self._gcp_target(resource))
        except BootstrapResourceTargetInvalidError:
            self._invalid_target(job)
            return
        except GcpAdapterError as error:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                error.error_code,
                error.public_message,
            )
            return
        try:
            if job.status is BootstrapJobStatus.APPLYING:
                adapter.ensure_temporary_label(
                    job.temporary_label_key,
                    job.temporary_label_value,
                )
                adapter.ensure_assignment(
                    assignment_id=job.provider_job_id or _assignment_id(job.job_id),
                    label_key=job.temporary_label_key,
                    label_value=job.temporary_label_value,
                    runner_url=runner_public_url(job.job_id),
                    runner_sha256=job.runner_sha256,
                    job_id=str(job.job_id),
                )
                job.status = BootstrapJobStatus.RUNNING
                job.error_code = None
                job.error_message = None
                self._session.commit()
                return

            now = datetime.now(UTC)
            if now >= job.deadline_at:
                self._cleanup_gcp(adapter, job)
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    "BOOTSTRAP_JOB_TIMEOUT",
                    "The bootstrap job did not finish before its deadline.",
                )
                return
            compliance = adapter.compliance(
                job.provider_job_id or _assignment_id(job.job_id)
            )
            if compliance is None:
                job.updated_at = now
                self._session.commit()
                return
            self._cleanup_gcp(adapter, job)
            if compliance:
                self._complete(job, BootstrapJobStatus.SUCCEEDED, None, None)
            else:
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    "BOOTSTRAP_ENFORCEMENT_FAILED",
                    "GCP reported that the bootstrap policy was not compliant.",
                )
        except GcpAdapterError as error:
            self._handle_gcp_error(adapter, job, error)
        except Exception:
            try:
                self._cleanup_gcp(adapter, job)
            except Exception:
                job.error_code = "BOOTSTRAP_CLEANUP_PENDING"
                job.error_message = (
                    "An internal error occurred and cleanup will be retried."
                )
                self._session.commit()
                return
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "BOOTSTRAP_INTERNAL_ERROR",
                "The bootstrap job failed before completion.",
            )

    def _process_aws(
        self,
        job: BootstrapJobTable,
        resource: ResourceTargetTable,
    ) -> None:
        try:
            adapter = self._aws_adapter(resource)
        except AwsAdapterError as error:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                error.error_code,
                error.public_message,
            )
            return
        except BootstrapResourceTargetInvalidError:
            self._invalid_target(job)
            return
        except RuntimeError:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "AWS_FEDERATION_NOT_CONFIGURED",
                "AWS workload identity federation is not configured.",
            )
            return
        try:
            if job.status is BootstrapJobStatus.APPLYING:
                job.provider_job_id = adapter.send_runner(
                    runner_url=runner_public_url(job.job_id),
                    runner_sha256=job.runner_sha256,
                    job_id=str(job.job_id),
                    timeout_seconds=_timeout_seconds(),
                )
                job.status = BootstrapJobStatus.RUNNING
                job.error_code = None
                job.error_message = None
                self._session.commit()
                return

            now = datetime.now(UTC)
            if now >= job.deadline_at:
                if job.provider_job_id is not None:
                    adapter.cancel_command(job.provider_job_id)
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    "BOOTSTRAP_JOB_TIMEOUT",
                    "The bootstrap job did not finish before its deadline.",
                )
                return
            if job.provider_job_id is None:
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    "AWS_SSM_COMMAND_ID_MISSING",
                    "The AWS Systems Manager command ID is missing.",
                )
                return
            outcome = adapter.command_status(job.provider_job_id)
            if not outcome.complete:
                job.updated_at = now
                self._session.commit()
                return
            if outcome.succeeded:
                self._complete(job, BootstrapJobStatus.SUCCEEDED, None, None)
            else:
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    outcome.error_code or "AWS_SSM_COMMAND_FAILED",
                    outcome.error_message
                    or "The AWS Systems Manager command failed.",
                )
        except AwsAdapterError as error:
            if job.status is BootstrapJobStatus.APPLYING:
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    error.error_code,
                    error.public_message,
                )
                return
            job.error_code = error.error_code
            job.error_message = error.public_message
            self._session.commit()
        except Exception:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "BOOTSTRAP_INTERNAL_ERROR",
                "The bootstrap job failed before completion.",
            )

    @staticmethod
    def _cleanup_gcp(adapter: GcpBootstrapAdapter, job: BootstrapJobTable) -> None:
        adapter.delete_assignment(job.provider_job_id or _assignment_id(job.job_id))
        adapter.remove_temporary_label(
            job.temporary_label_key,
            job.temporary_label_value,
        )

    def _handle_gcp_error(
        self,
        adapter: GcpBootstrapAdapter,
        job: BootstrapJobTable,
        error: GcpAdapterError,
    ) -> None:
        if isinstance(error, GcpBootstrapConflictError):
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                error.error_code,
                error.public_message,
            )
            return
        if job.status is BootstrapJobStatus.APPLYING:
            try:
                self._cleanup_gcp(adapter, job)
            except GcpAdapterError as cleanup_error:
                job.error_code = cleanup_error.error_code
                job.error_message = cleanup_error.public_message
                self._session.commit()
                return
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                error.error_code,
                error.public_message,
            )
            return
        job.error_code = error.error_code
        job.error_message = error.public_message
        self._session.commit()

    def _invalid_target(self, job: BootstrapJobTable) -> None:
        self._complete(
            job,
            BootstrapJobStatus.FAILED,
            "INVALID_RESOURCE_TARGET",
            "The resource target does not have the required provider fields.",
        )

    def _complete(
        self,
        job: BootstrapJobTable,
        status: BootstrapJobStatus,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        job.completed_at = datetime.now(UTC)
        self._session.commit()
