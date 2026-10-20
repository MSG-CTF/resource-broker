"""Operator-owned placement, independent of agent-reported capacity and runtime type."""
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from app.db.tables import ReservationTable
from app.domain.enums import ReservationStatus, RuntimeType
from app.services.scheduler_settings import SchedulerConfigurationError


class DedicatedVMPlacement:
    def __init__(self, placements=None):
        self.placements = placements or {}

    @classmethod
    def from_environment(cls):
        path = os.getenv("BROKER_DEDICATED_VM_PLACEMENTS_FILE", "").strip()
        if not path:
            return cls()
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 65536:
                raise ValueError()
            data = json.loads(raw)
            if set(data) != {"schema_version", "placements"} or data["schema_version"] != 1:
                raise ValueError()
            placements = {}
            for row in data["placements"]:
                if set(row) != {"target_id", "challenge_ids"} or not row["target_id"] or row["target_id"] in placements:
                    raise ValueError()
                ids = frozenset(UUID(value) for value in row["challenge_ids"])
                if not ids or any(value.int == 0 for value in ids):
                    raise ValueError()
                placements[row["target_id"]] = ids
            return cls(placements)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise SchedulerConfigurationError("invalid dedicated VM placement policy") from error

    def allows(self, resource, challenge_id):
        challenge_id = UUID(str(challenge_id)) if challenge_id is not None else None
        required = any(challenge_id in ids for ids in self.placements.values())
        if resource.runtime_type == RuntimeType.VM:
            return challenge_id in self.placements.get(resource.target_id, ())
        return not required and resource.target_id not in self.placements

    @staticmethod
    def busy(session, resource, now):
        if resource.runtime_type != RuntimeType.VM:
            return False
        # A newer observation must never hide a committed VM's exclusive lease.
        statement = select(func.count()).select_from(ReservationTable).where(
            ReservationTable.resource_target_id == resource.resource_target_id,
            or_(ReservationTable.status == ReservationStatus.COMMITTED,
                and_(ReservationTable.status == ReservationStatus.HELD,
                     ReservationTable.expires_at > now)),
        )
        return session.scalar(statement) > 0
