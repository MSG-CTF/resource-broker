from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.adapters.base import ProviderInstanceSummary
from app.db.tables import (
    ProviderAccountTable,
    ResourceTargetTable,
    RuntimeContainerTable,
)
from app.domain.enums import Provider


@dataclass(frozen=True, slots=True)
class ResourceSyncResult:
    created_count: int
    updated_count: int
    retired_count: int
    resources: tuple[ResourceTargetTable, ...]


@dataclass(frozen=True, slots=True)
class ResourceTargetListResult:
    total: int
    resources: tuple[ResourceTargetTable, ...]


class ProviderAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, account: ProviderAccountTable) -> ProviderAccountTable:
        self._session.add(account)
        self._session.flush()
        return account

    def get(self, account_id: UUID) -> ProviderAccountTable | None:
        return self._session.get(ProviderAccountTable, account_id)

    def delete(self, account: ProviderAccountTable) -> None:
        self._session.delete(account)
        self._session.flush()

    def list_all(self) -> tuple[ProviderAccountTable, ...]:
        statement = select(ProviderAccountTable).order_by(
            ProviderAccountTable.created_at,
            ProviderAccountTable.account_id,
        )
        return tuple(self._session.scalars(statement).all())


class ResourceTargetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_by_account_id(self, account_id: UUID) -> int:
        statement = delete(ResourceTargetTable).where(
            ResourceTargetTable.account_id == account_id
        )
        result = self._session.execute(statement)
        self._session.flush()
        return result.rowcount or 0

    def get(self, resource_target_id: UUID) -> ResourceTargetTable | None:
        return self._session.get(ResourceTargetTable, resource_target_id)

    def list_runtime_containers(
        self,
        resource_target_id: UUID,
    ) -> tuple[RuntimeContainerTable, ...]:
        statement = (
            select(RuntimeContainerTable)
            .where(
                RuntimeContainerTable.resource_target_id
                == resource_target_id
            )
            .order_by(
                RuntimeContainerTable.namespace,
                RuntimeContainerTable.pod_name,
                RuntimeContainerTable.container_name,
                RuntimeContainerTable.container_id,
            )
        )
        return tuple(self._session.scalars(statement).all())

    def list_all(
        self,
        *,
        provider: Provider | None,
        account_id: UUID | None,
        include_retired: bool,
        limit: int,
        offset: int,
    ) -> ResourceTargetListResult:
        filters = []
        if provider is not None:
            filters.append(ProviderAccountTable.provider == provider)
        if account_id is not None:
            filters.append(ResourceTargetTable.account_id == account_id)
        if not include_retired:
            filters.append(ResourceTargetTable.retired_at.is_(None))

        count_statement = (
            select(func.count())
            .select_from(ResourceTargetTable)
            .join(ProviderAccountTable)
            .where(*filters)
        )
        total = self._session.scalar(count_statement) or 0

        statement = (
            select(ResourceTargetTable)
            .join(ProviderAccountTable)
            .options(joinedload(ResourceTargetTable.account))
            .where(*filters)
            .order_by(
                ProviderAccountTable.provider,
                ProviderAccountTable.display_name,
                ResourceTargetTable.provider_scope_id,
                ResourceTargetTable.region,
                ResourceTargetTable.zone,
                ResourceTargetTable.instance_name,
                ResourceTargetTable.resource_target_id,
            )
            .limit(limit)
            .offset(offset)
        )
        resources = tuple(self._session.scalars(statement).all())
        return ResourceTargetListResult(
            total=total,
            resources=resources,
        )

    def sync_instances(
        self,
        *,
        account_id: UUID,
        scope_ids: tuple[str, ...],
        instances: tuple[ProviderInstanceSummary, ...],
        observed_at: datetime,
    ) -> ResourceSyncResult:
        statement = select(ResourceTargetTable).where(
            ResourceTargetTable.account_id == account_id,
            ResourceTargetTable.provider_scope_id.in_(scope_ids),
        )
        existing_resources = tuple(self._session.scalars(statement).all())
        existing_by_key = {
            (resource.provider_scope_id, resource.provider_instance_id): resource
            for resource in existing_resources
        }

        seen_keys: set[tuple[str, str]] = set()
        synchronized_resources: list[ResourceTargetTable] = []
        created_count = 0
        updated_count = 0

        for instance in instances:
            key = (
                instance.provider_scope_id,
                instance.provider_instance_id,
            )
            seen_keys.add(key)
            resource = existing_by_key.get(key)

            if resource is None:
                resource = ResourceTargetTable(
                    account_id=account_id,
                    provider_instance_id=instance.provider_instance_id,
                    provider_scope_id=instance.provider_scope_id,
                    instance_name=instance.name,
                    provider_instance_state=instance.status,
                    provider_machine_type=instance.machine_type,
                    private_ip=instance.internal_ip,
                    public_ip=instance.external_ip,
                    region=instance.region,
                    zone=instance.zone,
                    architecture=instance.architecture,
                    provider_capacity_cpu_millicores=(
                        instance.provider_capacity_cpu_millicores
                    ),
                    provider_capacity_memory_mib=(
                        instance.provider_capacity_memory_mib
                    ),
                    provider_capacity_storage_mib=(
                        instance.provider_capacity_storage_mib
                    ),
                    observed_at=observed_at,
                    last_seen_at=observed_at,
                )
                self._session.add(resource)
                created_count += 1
            else:
                resource.instance_name = instance.name
                resource.provider_instance_state = instance.status
                resource.provider_machine_type = instance.machine_type
                resource.private_ip = instance.internal_ip
                resource.public_ip = instance.external_ip
                resource.region = instance.region
                resource.zone = instance.zone
                if instance.architecture is not None:
                    resource.architecture = instance.architecture
                if instance.provider_capacity_cpu_millicores is not None:
                    resource.provider_capacity_cpu_millicores = (
                        instance.provider_capacity_cpu_millicores
                    )
                if instance.provider_capacity_memory_mib is not None:
                    resource.provider_capacity_memory_mib = (
                        instance.provider_capacity_memory_mib
                    )
                if instance.provider_capacity_storage_mib is not None:
                    resource.provider_capacity_storage_mib = (
                        instance.provider_capacity_storage_mib
                    )
                resource.observed_at = observed_at
                resource.last_seen_at = observed_at
                resource.retired_at = None
                updated_count += 1

            synchronized_resources.append(resource)

        retired_count = 0
        for resource in existing_resources:
            key = (
                resource.provider_scope_id,
                resource.provider_instance_id,
            )
            if key in seen_keys or resource.retired_at is not None:
                continue
            resource.retired_at = observed_at
            retired_count += 1

        self._session.flush()
        synchronized_resources.sort(
            key=lambda resource: (
                resource.provider_scope_id or "",
                resource.zone or "",
                resource.instance_name or "",
            )
        )
        return ResourceSyncResult(
            created_count=created_count,
            updated_count=updated_count,
            retired_count=retired_count,
            resources=tuple(synchronized_resources),
        )
