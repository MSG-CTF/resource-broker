from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable, RuntimeContainerTable
from app.domain.enums import RuntimeType


@dataclass(frozen=True, slots=True)
class RuntimeContainerSnapshot:
    container_id: str
    container_name: str
    pod_name: str | None
    namespace: str | None
    status: str
    cpu_request_millicores: int | None
    memory_request_mib: int | None
    ephemeral_storage_request_mib: int | None
    cpu_usage_millicores: int | None
    memory_usage_mib: int | None
    storage_usage_mib: int | None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotWriteResult:
    created_count: int
    updated_count: int
    deleted_count: int


class RuntimeObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resource_target_for_update(
        self,
        resource_target_id: UUID,
    ) -> ResourceTargetTable | None:
        statement = (
            select(ResourceTargetTable)
            .where(
                ResourceTargetTable.resource_target_id == resource_target_id
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def apply_snapshot(
        self,
        *,
        resource: ResourceTargetTable,
        observed_at: datetime,
        accepted_at: datetime,
        snapshot_complete: bool,
        runtime_type: RuntimeType,
        runtime_target_id: str,
        runtime_ready: bool,
        allocatable_cpu_millicores: int,
        allocatable_memory_mib: int,
        allocatable_ephemeral_storage_mib: int,
        runtime_cpu_usage_millicores: int | None,
        runtime_memory_usage_mib: int | None,
        containers: tuple[RuntimeContainerSnapshot, ...],
    ) -> RuntimeSnapshotWriteResult:
        statement = select(RuntimeContainerTable).where(
            RuntimeContainerTable.resource_target_id
            == resource.resource_target_id
        )
        existing_containers = tuple(self._session.scalars(statement).all())
        existing_by_id = {
            item.container_id: item for item in existing_containers
        }

        seen_container_ids: set[str] = set()
        created_count = 0
        updated_count = 0

        for snapshot in containers:
            seen_container_ids.add(snapshot.container_id)
            container = existing_by_id.get(snapshot.container_id)
            if container is None:
                container = RuntimeContainerTable(
                    resource_target_id=resource.resource_target_id,
                    container_id=snapshot.container_id,
                    container_name=snapshot.container_name,
                    pod_name=snapshot.pod_name,
                    namespace=snapshot.namespace,
                    status=snapshot.status,
                    cpu_request_millicores=snapshot.cpu_request_millicores,
                    memory_request_mib=snapshot.memory_request_mib,
                    ephemeral_storage_request_mib=(
                        snapshot.ephemeral_storage_request_mib
                    ),
                    cpu_usage_millicores=snapshot.cpu_usage_millicores,
                    memory_usage_mib=snapshot.memory_usage_mib,
                    storage_usage_mib=snapshot.storage_usage_mib,
                    observed_at=observed_at,
                )
                self._session.add(container)
                created_count += 1
                continue

            container.container_name = snapshot.container_name
            container.pod_name = snapshot.pod_name
            container.namespace = snapshot.namespace
            container.status = snapshot.status
            container.cpu_request_millicores = snapshot.cpu_request_millicores
            container.memory_request_mib = snapshot.memory_request_mib
            container.ephemeral_storage_request_mib = (
                snapshot.ephemeral_storage_request_mib
            )
            container.cpu_usage_millicores = snapshot.cpu_usage_millicores
            container.memory_usage_mib = snapshot.memory_usage_mib
            container.storage_usage_mib = snapshot.storage_usage_mib
            container.observed_at = observed_at
            updated_count += 1

        deleted_count = 0
        if snapshot_complete:
            for container in existing_containers:
                if container.container_id in seen_container_ids:
                    continue
                self._session.delete(container)
                deleted_count += 1

        resource.runtime_type = runtime_type
        resource.target_id = runtime_target_id
        resource.ready = runtime_ready
        resource.allocatable_cpu_millicores = allocatable_cpu_millicores
        resource.allocatable_memory_mib = allocatable_memory_mib
        resource.allocatable_ephemeral_storage_mib = (
            allocatable_ephemeral_storage_mib
        )
        resource.runtime_cpu_usage_millicores = runtime_cpu_usage_millicores
        resource.runtime_memory_usage_mib = runtime_memory_usage_mib
        resource.runtime_observed_at = observed_at
        resource.runtime_last_seen_at = accepted_at

        self._session.flush()
        return RuntimeSnapshotWriteResult(
            created_count=created_count,
            updated_count=updated_count,
            deleted_count=deleted_count,
        )
