from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.gcp import GcpAdapterError
from app.adapters.gcp_windows_worker import (
    GcpWindowsWorkerAdapter,
    GcpWindowsWorkerPool,
    ManagedWindowsInstance,
)
from app.db.tables import (
    WindowsWorkerLeaseTable,
    WindowsWorkerPoolTable,
    WindowsWorkerTable,
)
from app.domain.enums import WindowsWorkerLeaseStatus, WindowsWorkerStatus
from app.repositories.windows_workers import WindowsWorkerRepository


class WindowsWorkerConfigurationError(RuntimeError):
    pass


class DuplicateWindowsWorkerPoolError(ValueError):
    pass


class NoWindowsWorkerAvailableError(RuntimeError):
    pass


class WindowsWorkerLeaseNotFoundError(LookupError):
    pass


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as error:
        raise WindowsWorkerConfigurationError(
            f"{name} must be a positive integer"
        ) from error
    if value <= 0:
        raise WindowsWorkerConfigurationError(
            f"{name} must be a positive integer"
        )
    return value


@dataclass(frozen=True, slots=True)
class WindowsWorkerSettings:
    lease_ttl_seconds: int
    poll_seconds: int
    sync_seconds: int
    recreate_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> WindowsWorkerSettings:
        return cls(
            lease_ttl_seconds=_positive_int(
                "WINDOWS_WORKER_LEASE_TTL_SECONDS",
                7200,
            ),
            poll_seconds=_positive_int(
                "WINDOWS_WORKER_POLL_SECONDS",
                10,
            ),
            sync_seconds=_positive_int(
                "WINDOWS_WORKER_SYNC_SECONDS",
                30,
            ),
            recreate_timeout_seconds=_positive_int(
                "WINDOWS_WORKER_RECREATE_TIMEOUT_SECONDS",
                1800,
            ),
        )


@dataclass(frozen=True, slots=True)
class LeaseOutcome:
    lease: WindowsWorkerLeaseTable
    replayed: bool


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    lease: WindowsWorkerLeaseTable
    replayed: bool


class WindowsWorkerService:
    def __init__(
        self,
        session: Session,
        settings: WindowsWorkerSettings | None = None,
    ) -> None:
        self._session = session
        self._repository = WindowsWorkerRepository(session)
        self._settings = settings

    @property
    def settings(self) -> WindowsWorkerSettings:
        if self._settings is None:
            self._settings = WindowsWorkerSettings.from_environment()
        return self._settings

    def create_pool(
        self,
        *,
        project_id: str,
        zone: str,
        managed_instance_group: str,
        display_name: str | None,
    ) -> WindowsWorkerPoolTable:
        try:
            pool = self._repository.add_pool(
                project_id=project_id,
                zone=zone,
                managed_instance_group=managed_instance_group,
                display_name=display_name,
            )
            self._session.commit()
            return pool
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateWindowsWorkerPoolError from error

    def list_pools(self) -> tuple[WindowsWorkerPoolTable, ...]:
        return self._repository.list_pools()

    def lease(self, idempotency_key: UUID) -> LeaseOutcome:
        existing = self._repository.lease_by_idempotency_key(
            idempotency_key
        )
        if existing is not None:
            return LeaseOutcome(existing, replayed=True)

        now = datetime.now(UTC)
        worker = self._repository.lock_ready_worker(
            now - timedelta(seconds=self.settings.sync_seconds * 2)
        )
        if worker is None:
            raise NoWindowsWorkerAvailableError

        expires_at = now + timedelta(
            seconds=self.settings.lease_ttl_seconds
        )
        try:
            lease = self._repository.add_lease(
                worker=worker,
                idempotency_key=idempotency_key,
                expires_at=expires_at,
            )
            self._session.commit()
            return LeaseOutcome(lease, replayed=False)
        except IntegrityError:
            self._session.rollback()
            existing = self._repository.lease_by_idempotency_key(
                idempotency_key
            )
            if existing is not None:
                return LeaseOutcome(existing, replayed=True)
            raise

    def complete(self, lease_id: UUID) -> CompletionOutcome:
        lease = self._repository.lease_by_id(
            lease_id,
            for_update=True,
        )
        if lease is None:
            raise WindowsWorkerLeaseNotFoundError
        if lease.status != WindowsWorkerLeaseStatus.LEASED:
            return CompletionOutcome(lease, replayed=True)

        self._repository.start_recycling(
            lease,
            now=datetime.now(UTC),
            status=WindowsWorkerLeaseStatus.COMPLETED,
        )
        self._session.commit()
        return CompletionOutcome(lease, replayed=False)

    def process_next(self) -> bool:
        now = datetime.now(UTC)
        expired = self._repository.next_expired_lease(now)
        if expired is not None:
            self._repository.start_recycling(
                expired,
                now=now,
                status=WindowsWorkerLeaseStatus.EXPIRED,
            )
            self._session.commit()
            return True

        worker = self._repository.next_recreating_worker(
            now - timedelta(seconds=self.settings.poll_seconds)
        )
        if worker is not None:
            self._reconcile_worker(worker, now)
            return True

        pool = self._repository.next_pool_to_sync(
            now - timedelta(seconds=self.settings.sync_seconds)
        )
        if pool is None:
            return False

        pool_id = pool.pool_id
        try:
            self._sync_pool(pool, now)
        except GcpAdapterError as error:
            self._session.rollback()
            failed_pool = self._repository.pool_by_id(pool_id)
            if failed_pool is not None:
                failed_pool.error_code = error.error_code
                failed_pool.error_message = error.public_message
                failed_pool.updated_at = now
                self._session.commit()
        return True

    def _adapter(
        self,
        pool: WindowsWorkerPoolTable,
    ) -> GcpWindowsWorkerAdapter:
        # ponytail: zonal MIG only; add the regional client if multi-zone
        # Windows worker pools are introduced.
        return GcpWindowsWorkerAdapter.from_adc(
            GcpWindowsWorkerPool(
                project_id=pool.project_id,
                zone=pool.zone,
                managed_instance_group=pool.managed_instance_group,
            )
        )

    def _reconcile_worker(
        self,
        worker: WindowsWorkerTable,
        now: datetime,
    ) -> None:
        if self._recreate_timed_out(worker, now):
            self._mark_recreate_failed(worker, now)
            return

        try:
            adapter = self._adapter(worker.pool)
            if worker.recreate_requested_at is None:
                if worker.recycle_request_id is None:
                    worker.status = WindowsWorkerStatus.FAILED
                    worker.error_code = "WINDOWS_WORKER_RECYCLE_STATE_INVALID"
                    worker.error_message = (
                        "The Windows worker recycle state is invalid."
                    )
                    worker.updated_at = now
                    self._session.commit()
                    return
                adapter.recreate(
                    worker.instance_name,
                    worker.recycle_request_id,
                )
                worker.recreate_requested_at = now
                worker.updated_at = now
                worker.error_code = None
                worker.error_message = None
                self._session.commit()
                return

            managed = {
                item.instance_name: item
                for item in adapter.list_managed_instances()
            }.get(worker.instance_name)
            worker.updated_at = now
            if managed is None:
                worker.error_code = "WINDOWS_WORKER_NOT_IN_MIG"
                worker.error_message = (
                    "The Windows worker is not currently listed by its MIG."
                )
                self._session.commit()
                return

            worker.last_seen_at = now
            if not self._is_recreated_and_healthy(worker, managed):
                self._session.commit()
                return

            private_ip = adapter.private_ip(worker.instance_name)
            if private_ip is None:
                worker.error_code = "WINDOWS_WORKER_PRIVATE_IP_UNAVAILABLE"
                worker.error_message = (
                    "The recreated Windows worker has no private IP address."
                )
                self._session.commit()
                return

            worker.instance_id = managed.instance_id
            worker.private_ip = private_ip
            worker.status = WindowsWorkerStatus.READY
            worker.ready_at = now
            worker.recycle_request_id = None
            worker.recycle_source_instance_id = None
            worker.recycle_started_at = None
            worker.recreate_requested_at = None
            worker.error_code = None
            worker.error_message = None
            self._session.commit()
        except GcpAdapterError as error:
            worker.error_code = error.error_code
            worker.error_message = error.public_message
            worker.updated_at = now
            if self._recreate_timed_out(worker, now):
                self._mark_recreate_failed(worker, now)
            else:
                self._session.commit()

    def _sync_pool(
        self,
        pool: WindowsWorkerPoolTable,
        now: datetime,
    ) -> None:
        adapter = self._adapter(pool)
        managed = adapter.list_managed_instances()
        snapshots = [
            (
                item,
                adapter.private_ip(item.instance_name)
                if item.running and item.idle and item.healthy
                else None,
            )
            for item in managed
        ]

        workers = {
            item.instance_name: item
            for item in self._repository.workers_for_pool(pool.pool_id)
        }
        seen: set[str] = set()
        for remote, private_ip in snapshots:
            seen.add(remote.instance_name)
            worker = workers.get(remote.instance_name)
            if worker is None:
                worker = self._repository.add_worker(
                    pool=pool,
                    instance_name=remote.instance_name,
                )
            if worker.status in {
                WindowsWorkerStatus.LEASED,
                WindowsWorkerStatus.RECREATING,
            } or worker.recycle_started_at is not None:
                continue

            worker.instance_id = remote.instance_id
            worker.private_ip = private_ip
            worker.last_seen_at = now
            worker.error_code = None
            worker.error_message = None
            if remote.running and remote.idle and remote.healthy and private_ip:
                if worker.status != WindowsWorkerStatus.READY:
                    worker.ready_at = now
                worker.status = WindowsWorkerStatus.READY
            else:
                worker.status = WindowsWorkerStatus.PROVISIONING
                worker.ready_at = None

        for name, worker in workers.items():
            if name in seen or worker.status in {
                WindowsWorkerStatus.LEASED,
                WindowsWorkerStatus.RECREATING,
            } or worker.recycle_started_at is not None:
                continue
            worker.status = WindowsWorkerStatus.FAILED
            worker.ready_at = None
            worker.error_code = "WINDOWS_WORKER_NOT_IN_MIG"
            worker.error_message = (
                "The Windows worker is not currently listed by its MIG."
            )

        pool.last_synced_at = now
        pool.error_code = None
        pool.error_message = None
        self._session.commit()

    def _recreate_timed_out(
        self,
        worker: WindowsWorkerTable,
        now: datetime,
    ) -> bool:
        return (
            worker.recycle_started_at is not None
            and now - worker.recycle_started_at
            >= timedelta(seconds=self.settings.recreate_timeout_seconds)
        )

    def _mark_recreate_failed(
        self,
        worker: WindowsWorkerTable,
        now: datetime,
    ) -> None:
        worker.status = WindowsWorkerStatus.FAILED
        worker.error_code = "WINDOWS_WORKER_RECREATE_TIMEOUT"
        worker.error_message = (
            "The Windows worker did not become healthy after recreation."
        )
        worker.updated_at = now
        self._session.commit()

    @staticmethod
    def _is_recreated_and_healthy(
        worker: WindowsWorkerTable,
        managed: ManagedWindowsInstance,
    ) -> bool:
        return (
            managed.instance_id != worker.recycle_source_instance_id
            and managed.running
            and managed.idle
            and managed.healthy
        )
