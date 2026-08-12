from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import os
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.gcp import GcpAdapterError
from app.adapters.gcp_bootstrap import (
    GcpBootstrapAdapter,
    GcpBootstrapConflictError,
    GcpBootstrapTarget,
)
from app.db.tables import BootstrapJobTable, ResourceTargetTable
from app.domain.enums import BootstrapAction, BootstrapJobStatus, Provider
from app.repositories.bootstrap_jobs import BootstrapJobRepository
from app.services.bootstrap_artifacts import (
    artifact_for_version,
    enrollment_audience,
    render_runner_script,
    runner_public_url,
    runner_sha256,
)


_LABEL_KEY = "msg-broker-bootstrap-job"


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
        gcp_adapter_factory: GcpBootstrapAdapterFactory = (
            GcpBootstrapAdapter.from_adc
        ),
    ) -> None:
        self._session = session
        self._jobs = BootstrapJobRepository(session)
        self._gcp_adapter_factory = gcp_adapter_factory

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
        if target.retired_at is not None or target.instance_name is None or target.zone is None:
            raise BootstrapResourceTargetInvalidError
        provider = target.account.provider
        if provider is not Provider.GCP:
            raise BootstrapProviderNotImplementedError(provider)
        if self._jobs.active_for_resource(resource_target_id) is not None:
            raise BootstrapJobConflictError

        artifact = artifact_for_version(bootstrap_version)
        now = datetime.now(UTC)
        job_id = uuid4()
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
            provider_job_id=_assignment_id(job_id),
            temporary_label_key=_LABEL_KEY,
            temporary_label_value=_label_value(job_id),
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

    def list_for_resource(self, resource_target_id: UUID) -> tuple[BootstrapJobTable, ...]:
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
                resource is not None
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

    def _target(self, resource: ResourceTargetTable) -> GcpBootstrapTarget:
        if resource.provider_scope_id is None or resource.zone is None or resource.instance_name is None:
            raise BootstrapResourceTargetInvalidError
        return GcpBootstrapTarget(
            project_id=resource.provider_scope_id,
            zone=resource.zone,
            instance_name=resource.instance_name,
            instance_id=resource.provider_instance_id,
        )

    def _process(self, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        resource = self._jobs.get_resource_target(job.resource_target_id)
        if resource is None:
            return
        try:
            target = self._target(resource)
        except BootstrapResourceTargetInvalidError:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "INVALID_RESOURCE_TARGET",
                "The resource target does not have the required provider fields.",
            )
            return
        try:
            adapter = self._gcp_adapter_factory(target)
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
                self._cleanup(adapter, job)
                self._complete(
                    job,
                    BootstrapJobStatus.FAILED,
                    "BOOTSTRAP_JOB_TIMEOUT",
                    "The bootstrap job did not finish before its deadline.",
                )
                return

            compliance = adapter.compliance(job.provider_job_id or _assignment_id(job.job_id))
            if compliance is None:
                job.updated_at = now
                self._session.commit()
                return
            self._cleanup(adapter, job)
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
            self._handle_provider_error(adapter, job, error)
        except BootstrapResourceTargetInvalidError:
            self._complete(
                job,
                BootstrapJobStatus.FAILED,
                "INVALID_RESOURCE_TARGET",
                "The resource target does not have the required provider fields.",
            )
        except Exception:
            try:
                self._cleanup(adapter, job)
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

    def _cleanup(self, adapter: GcpBootstrapAdapter, job: BootstrapJobTable) -> None:
        adapter.delete_assignment(job.provider_job_id or _assignment_id(job.job_id))
        adapter.remove_temporary_label(
            job.temporary_label_key,
            job.temporary_label_value,
        )

    def _handle_provider_error(
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
                self._cleanup(adapter, job)
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
