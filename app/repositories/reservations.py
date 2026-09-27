from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.db.tables import (
    ProviderAccountTable,
    ReservationTable,
    ResourceTargetTable,
)
from app.domain.enums import Architecture, ReservationStatus
from app.domain.observation import OBSERVATION_CLOCK_SKEW
from app.domain.provider_state import RUNNING_PROVIDER_INSTANCE_STATE


@dataclass(frozen=True, slots=True)
class ReservedCapacity:
    cpu_millicores: int
    memory_mib: int
    ephemeral_storage_mib: int


class CandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_eligible_resources(
        self,
        *,
        architecture: Architecture,
        observed_after: datetime,
        observed_before: datetime,
    ) -> tuple[ResourceTargetTable, ...]:
        statement = (
            select(ResourceTargetTable)
            .join(ProviderAccountTable)
            .options(joinedload(ResourceTargetTable.account))
            .where(
                ProviderAccountTable.enabled.is_(True),
                ResourceTargetTable.enabled.is_(True),
                ResourceTargetTable.ready.is_(True),
                ResourceTargetTable.retired_at.is_(None),
                ResourceTargetTable.provider_instance_state
                == RUNNING_PROVIDER_INSTANCE_STATE,
                ResourceTargetTable.architecture == architecture,
                ResourceTargetTable.runtime_type.is_not(None),
                ResourceTargetTable.target_id.is_not(None),
                ResourceTargetTable.runtime_last_seen_at.is_not(None),
                ResourceTargetTable.runtime_last_seen_at > observed_after,
                ResourceTargetTable.runtime_observed_at.is_not(None),
                ResourceTargetTable.runtime_observed_at > observed_after,
                ResourceTargetTable.runtime_observed_at <= observed_before,
                ResourceTargetTable.provider_capacity_cpu_millicores.is_not(
                    None
                ),
                ResourceTargetTable.provider_capacity_memory_mib.is_not(None),
                ResourceTargetTable.runtime_cpu_usage_millicores.is_not(None),
                ResourceTargetTable.runtime_memory_usage_mib.is_not(None),
                ResourceTargetTable.allocatable_cpu_millicores.is_not(None),
                ResourceTargetTable.allocatable_memory_mib.is_not(None),
                ResourceTargetTable.allocatable_ephemeral_storage_mib.is_not(
                    None
                ),
            )
        )
        return tuple(self._session.scalars(statement).all())

    def deductible_usage_by_target(
        self,
        *,
        resource_target_ids: tuple[UUID, ...],
        now: datetime,
    ) -> dict[UUID, ReservedCapacity]:
        if not resource_target_ids:
            return {}

        statement = (
            select(
                ReservationTable.resource_target_id,
                func.sum(ReservationTable.cpu_millicores),
                func.sum(ReservationTable.memory_mib),
                func.sum(ReservationTable.ephemeral_storage_mib),
            )
            .join(ResourceTargetTable)
            .where(
                ReservationTable.resource_target_id.in_(
                    resource_target_ids
                ),
                _deductible_reservation_filter(now),
            )
            .group_by(ReservationTable.resource_target_id)
        )
        return {
            resource_target_id: ReservedCapacity(
                cpu_millicores=int(cpu or 0),
                memory_mib=int(memory or 0),
                ephemeral_storage_mib=int(storage or 0),
            )
            for resource_target_id, cpu, memory, storage in self._session.execute(
                statement
            )
        }


class ReservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reservation: ReservationTable) -> ReservationTable:
        self._session.add(reservation)
        self._session.flush()
        return reservation

    def has_active_for_account(self, account_id: UUID) -> bool:
        statement = (
            select(ReservationTable.reservation_id)
            .join(ResourceTargetTable)
            .where(
                ResourceTargetTable.account_id == account_id,
                or_(
                    ReservationTable.status == ReservationStatus.COMMITTED,
                    and_(
                        ReservationTable.status == ReservationStatus.HELD,
                        ReservationTable.expires_at > func.now(),
                    ),
                ),
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None

    def get(
        self,
        reservation_id: UUID,
        *,
        for_update: bool = False,
    ) -> ReservationTable | None:
        statement = (
            select(ReservationTable)
            .options(joinedload(ReservationTable.resource_target))
            .where(ReservationTable.reservation_id == reservation_id)
        )
        if for_update:
            statement = statement.with_for_update(of=ReservationTable)
        return self._session.scalar(statement)

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> ReservationTable | None:
        statement = (
            select(ReservationTable)
            .options(joinedload(ReservationTable.resource_target))
            .where(ReservationTable.idempotency_key == idempotency_key)
        )
        return self._session.scalar(statement)

    def get_active_for_instance(
        self,
        *,
        team_id: UUID,
        instance_id: str,
    ) -> ReservationTable | None:
        statement = select(ReservationTable).where(
            ReservationTable.team_id == team_id,
            ReservationTable.instance_id == instance_id,
            ReservationTable.status.in_(
                (ReservationStatus.HELD, ReservationStatus.COMMITTED)
            ),
        )
        return self._session.scalar(statement)

    def get_resource_for_update(
        self,
        resource_target_id: UUID,
    ) -> ResourceTargetTable | None:
        statement = (
            select(ResourceTargetTable)
            .options(joinedload(ResourceTargetTable.account))
            .where(
                ResourceTargetTable.resource_target_id
                == resource_target_id
            )
            .with_for_update(of=ResourceTargetTable)
        )
        return self._session.scalar(statement)

    def expire_held_for_target(
        self,
        *,
        resource_target_id: UUID,
        now: datetime,
    ) -> int:
        statement = (
            update(ReservationTable)
            .where(
                ReservationTable.resource_target_id == resource_target_id,
                ReservationTable.status == ReservationStatus.HELD,
                ReservationTable.expires_at <= now,
            )
            .values(status=ReservationStatus.EXPIRED, updated_at=now)
        )
        result = self._session.execute(statement)
        self._session.flush()
        return result.rowcount or 0

    def expire_held(self, reservation_id: UUID, *, now: datetime) -> bool:
        # Recheck status in the database after any concurrent writer finishes;
        # never overwrite COMMITTED using a previously loaded HELD snapshot.
        statement = (
            update(ReservationTable)
            .where(
                ReservationTable.reservation_id == reservation_id,
                ReservationTable.status == ReservationStatus.HELD,
                ReservationTable.expires_at <= now,
            )
            .values(status=ReservationStatus.EXPIRED, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        result = self._session.execute(statement)
        return result.rowcount == 1

    def expire_held_for_instance(
        self,
        *,
        team_id: UUID,
        instance_id: str,
        now: datetime,
    ) -> int:
        statement = (
            update(ReservationTable)
            .where(
                ReservationTable.team_id == team_id,
                ReservationTable.instance_id == instance_id,
                ReservationTable.status == ReservationStatus.HELD,
                ReservationTable.expires_at <= now,
            )
            .values(status=ReservationStatus.EXPIRED, updated_at=now)
        )
        result = self._session.execute(statement)
        self._session.flush()
        return result.rowcount or 0

    def deductible_usage_for_target(
        self,
        *,
        resource_target_id: UUID,
        now: datetime,
    ) -> ReservedCapacity:
        statement = (
            select(
                func.sum(ReservationTable.cpu_millicores),
                func.sum(ReservationTable.memory_mib),
                func.sum(ReservationTable.ephemeral_storage_mib),
            )
            .join(ResourceTargetTable)
            .where(
                ReservationTable.resource_target_id == resource_target_id,
                _deductible_reservation_filter(now),
            )
        )
        cpu, memory, storage = self._session.execute(statement).one()
        return ReservedCapacity(
            cpu_millicores=int(cpu or 0),
            memory_mib=int(memory or 0),
            ephemeral_storage_mib=int(storage or 0),
        )


def _deductible_reservation_filter(now: datetime):
    return or_(
        and_(
            ReservationTable.status == ReservationStatus.HELD,
            ReservationTable.expires_at.is_not(None),
            ReservationTable.expires_at > now,
        ),
        and_(
            ReservationTable.status == ReservationStatus.COMMITTED,
            or_(
                ResourceTargetTable.runtime_observed_at.is_(None),
                ReservationTable.committed_at.is_(None),
                ReservationTable.committed_at + OBSERVATION_CLOCK_SKEW
                >= ResourceTargetTable.runtime_observed_at,
            ),
        ),
    )
