from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.tables import (
    WindowsWorkerLeaseTable,
    WindowsWorkerPoolTable,
    WindowsWorkerTable,
)
from app.domain.enums import WindowsWorkerLeaseStatus, WindowsWorkerStatus


class WindowsWorkerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_pool(
        self,
        *,
        project_id: str,
        zone: str,
        managed_instance_group: str,
        display_name: str | None,
    ) -> WindowsWorkerPoolTable:
        pool = WindowsWorkerPoolTable(
            project_id=project_id,
            zone=zone,
            managed_instance_group=managed_instance_group,
            display_name=display_name,
        )
        self.session.add(pool)
        self.session.flush()
        return pool

    def list_pools(self) -> tuple[WindowsWorkerPoolTable, ...]:
        statement = (
            select(WindowsWorkerPoolTable)
            .options(selectinload(WindowsWorkerPoolTable.workers))
            .order_by(
                WindowsWorkerPoolTable.created_at,
                WindowsWorkerPoolTable.pool_id,
            )
        )
        return tuple(self.session.scalars(statement).all())

    def pool_by_id(self, pool_id: UUID) -> WindowsWorkerPoolTable | None:
        return self.session.get(WindowsWorkerPoolTable, pool_id)

    def add_worker(
        self,
        *,
        pool: WindowsWorkerPoolTable,
        instance_name: str,
    ) -> WindowsWorkerTable:
        worker = WindowsWorkerTable(
            pool=pool,
            instance_name=instance_name,
        )
        self.session.add(worker)
        return worker

    def lease_by_idempotency_key(
        self,
        idempotency_key: UUID,
    ) -> WindowsWorkerLeaseTable | None:
        statement = (
            select(WindowsWorkerLeaseTable)
            .options(joinedload(WindowsWorkerLeaseTable.worker))
            .where(
                WindowsWorkerLeaseTable.idempotency_key == idempotency_key
            )
        )
        return self.session.scalar(statement)

    def lease_by_id(
        self,
        lease_id: UUID,
        *,
        for_update: bool = False,
    ) -> WindowsWorkerLeaseTable | None:
        statement = (
            select(WindowsWorkerLeaseTable)
            .options(joinedload(WindowsWorkerLeaseTable.worker))
            .where(WindowsWorkerLeaseTable.lease_id == lease_id)
        )
        if for_update:
            statement = statement.with_for_update(
                of=WindowsWorkerLeaseTable
            )
        return self.session.scalar(statement)

    def lock_ready_worker(
        self,
        synced_after: datetime,
    ) -> WindowsWorkerTable | None:
        statement = (
            select(WindowsWorkerTable)
            .join(WindowsWorkerPoolTable)
            .where(
                WindowsWorkerPoolTable.enabled.is_(True),
                WindowsWorkerPoolTable.last_synced_at >= synced_after,
                WindowsWorkerTable.status == WindowsWorkerStatus.READY,
                WindowsWorkerTable.instance_id.is_not(None),
                WindowsWorkerTable.private_ip.is_not(None),
            )
            .order_by(
                WindowsWorkerTable.ready_at,
                WindowsWorkerTable.worker_id,
            )
            .with_for_update(of=WindowsWorkerTable, skip_locked=True)
            .limit(1)
        )
        return self.session.scalar(statement)

    def add_lease(
        self,
        *,
        worker: WindowsWorkerTable,
        idempotency_key: UUID,
        expires_at: datetime,
    ) -> WindowsWorkerLeaseTable:
        lease = WindowsWorkerLeaseTable(
            worker=worker,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        worker.status = WindowsWorkerStatus.LEASED
        self.session.add(lease)
        self.session.flush()
        return lease

    @staticmethod
    def start_recycling(
        lease: WindowsWorkerLeaseTable,
        *,
        now: datetime,
        status: WindowsWorkerLeaseStatus,
    ) -> None:
        lease.status = status
        lease.completed_at = now
        worker = lease.worker
        worker.status = WindowsWorkerStatus.RECREATING
        worker.recycle_request_id = uuid4()
        worker.recycle_source_instance_id = worker.instance_id
        worker.recycle_started_at = now
        worker.recreate_requested_at = None
        worker.ready_at = None
        worker.error_code = None
        worker.error_message = None

    def next_expired_lease(
        self,
        now: datetime,
    ) -> WindowsWorkerLeaseTable | None:
        statement = (
            select(WindowsWorkerLeaseTable)
            .options(joinedload(WindowsWorkerLeaseTable.worker))
            .where(
                WindowsWorkerLeaseTable.status
                == WindowsWorkerLeaseStatus.LEASED,
                WindowsWorkerLeaseTable.expires_at <= now,
            )
            .order_by(
                WindowsWorkerLeaseTable.expires_at,
                WindowsWorkerLeaseTable.lease_id,
            )
            .with_for_update(
                of=WindowsWorkerLeaseTable,
                skip_locked=True,
            )
            .limit(1)
        )
        return self.session.scalar(statement)

    def next_recreating_worker(
        self,
        updated_before: datetime,
    ) -> WindowsWorkerTable | None:
        statement = (
            select(WindowsWorkerTable)
            .options(joinedload(WindowsWorkerTable.pool))
            .where(
                WindowsWorkerTable.status
                == WindowsWorkerStatus.RECREATING,
                WindowsWorkerTable.updated_at <= updated_before,
            )
            .order_by(
                WindowsWorkerTable.updated_at,
                WindowsWorkerTable.worker_id,
            )
            .with_for_update(of=WindowsWorkerTable, skip_locked=True)
            .limit(1)
        )
        return self.session.scalar(statement)

    def next_pool_to_sync(
        self,
        synced_before: datetime,
    ) -> WindowsWorkerPoolTable | None:
        statement = (
            select(WindowsWorkerPoolTable)
            .where(
                WindowsWorkerPoolTable.enabled.is_(True),
                or_(
                    and_(
                        WindowsWorkerPoolTable.last_synced_at.is_(None),
                        WindowsWorkerPoolTable.error_code.is_(None),
                    ),
                    func.coalesce(
                        WindowsWorkerPoolTable.last_synced_at,
                        WindowsWorkerPoolTable.updated_at,
                    )
                    <= synced_before,
                ),
            )
            .order_by(
                WindowsWorkerPoolTable.last_synced_at.asc().nullsfirst(),
                WindowsWorkerPoolTable.pool_id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return self.session.scalar(statement)

    def workers_for_pool(
        self,
        pool_id: UUID,
    ) -> tuple[WindowsWorkerTable, ...]:
        statement = (
            select(WindowsWorkerTable)
            .where(WindowsWorkerTable.pool_id == pool_id)
            .with_for_update()
        )
        return tuple(self.session.scalars(statement).all())
