"""Reservation ledger for operators, independent of observed runtime usage."""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.tables import ReservationTable, ResourceTargetTable
from app.domain.enums import ReservationStatus


def _instance_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def allocation_snapshots(session: Session, target_ids: tuple) -> dict:
    if not target_ids:
        return {}
    collected_at = datetime.now(UTC)
    # Capacity and all active reservations come from one statement snapshot.
    # COMMITTED remains allocated even after telemetry starts observing it.
    # Candidate deduction intentionally has different semantics to avoid double counting usage.
    active = or_(
        ReservationTable.status == ReservationStatus.COMMITTED,
        and_(ReservationTable.status == ReservationStatus.HELD,
             ReservationTable.expires_at > collected_at),
    )
    statement = (
        select(ResourceTargetTable.resource_target_id,
               ResourceTargetTable.node_allocatable_cpu_millicores,
               ResourceTargetTable.node_allocatable_memory_mib,
               ReservationTable)
        .outerjoin(ReservationTable, and_(
            ReservationTable.resource_target_id == ResourceTargetTable.resource_target_id,
            active,
        ))
        .where(ResourceTargetTable.resource_target_id.in_(target_ids))
        .order_by(ResourceTargetTable.resource_target_id, ReservationTable.reservation_id)
    )
    snapshots = {}
    for target_id, cpu, memory, reservation in session.execute(statement):
        snapshot = snapshots.setdefault(target_id, {
            "collected_at": collected_at,
            "complete": True,
            "allocatable": {"cpu_millicores": cpu, "memory_mib": memory},
            "other_reserved": {"cpu_millicores": 0, "memory_mib": 0},
            "allocations": [],
        })
        if reservation is not None:
            snapshot["allocations"].append({
                "reservation_id": reservation.reservation_id,
                "instance_id": _instance_uuid(reservation.instance_id),
                "challenge_id": reservation.challenge_id,
                "team_id": reservation.team_id,
                "reservation_status": reservation.status,
                "expires_at": reservation.expires_at,
                "resources": {"cpu_millicores": (
                    reservation.deployed_cpu_millicores
                    if reservation.status is ReservationStatus.COMMITTED
                    and reservation.deployed_cpu_millicores is not None
                    and 0 < reservation.deployed_cpu_millicores <= reservation.cpu_millicores
                    else reservation.cpu_millicores
                ),
                              "memory_mib": reservation.memory_mib},
            })
    return snapshots
