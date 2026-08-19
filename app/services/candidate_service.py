from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable
from app.domain.enums import Architecture, CandidateQueryStatus
from app.repositories.reservations import (
    CandidateRepository,
    ReservedCapacity,
)
from app.services.scheduler_settings import SchedulerSettings


CANDIDATE_RESULT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    resource: ResourceTargetTable
    remaining_cpu_millicores: int
    remaining_memory_mib: int
    remaining_ephemeral_storage_mib: int
    fit_count: int
    valid_until: datetime


@dataclass(frozen=True, slots=True)
class CandidateQueryOutcome:
    generated_at: datetime
    status: CandidateQueryStatus
    candidates: tuple[CandidateMatch, ...]


class CandidateService:
    def __init__(
        self,
        session: Session,
        settings: SchedulerSettings | None = None,
    ) -> None:
        self._candidates = CandidateRepository(session)
        self._settings = settings or SchedulerSettings.from_environment()

    def query(
        self,
        *,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
        architecture: Architecture,
    ) -> CandidateQueryOutcome:
        now = datetime.now(UTC)
        observed_after = now - timedelta(
            seconds=self._settings.observation_stale_seconds
        )
        resources = self._candidates.list_eligible_resources(
            architecture=architecture,
            observed_after=observed_after,
        )
        usage_by_target = self._candidates.deductible_usage_by_target(
            resource_target_ids=tuple(
                resource.resource_target_id for resource in resources
            ),
            now=now,
        )

        matches: list[CandidateMatch] = []
        zero = ReservedCapacity(0, 0, 0)
        for resource in resources:
            reserved = usage_by_target.get(
                resource.resource_target_id,
                zero,
            )
            remaining_cpu = max(
                (resource.allocatable_cpu_millicores or 0)
                - reserved.cpu_millicores,
                0,
            )
            remaining_memory = max(
                (resource.allocatable_memory_mib or 0)
                - reserved.memory_mib,
                0,
            )
            remaining_storage = max(
                (resource.allocatable_ephemeral_storage_mib or 0)
                - reserved.ephemeral_storage_mib,
                0,
            )
            fit_count = min(
                remaining_cpu // cpu_millicores,
                remaining_memory // memory_mib,
                remaining_storage // ephemeral_storage_mib,
            )
            if fit_count < 1:
                continue

            runtime_last_seen_at = resource.runtime_last_seen_at
            if runtime_last_seen_at is None:
                continue
            valid_until = min(
                now
                + timedelta(
                    seconds=self._settings.candidate_validity_seconds
                ),
                runtime_last_seen_at
                + timedelta(
                    seconds=self._settings.observation_stale_seconds
                ),
            )
            matches.append(
                CandidateMatch(
                    resource=resource,
                    remaining_cpu_millicores=remaining_cpu,
                    remaining_memory_mib=remaining_memory,
                    remaining_ephemeral_storage_mib=remaining_storage,
                    fit_count=fit_count,
                    valid_until=valid_until,
                )
            )

        matches.sort(
            key=lambda item: (
                item.fit_count,
                item.remaining_cpu_millicores,
                item.remaining_memory_mib,
                item.resource.account.provider.value,
                str(item.resource.resource_target_id),
            )
        )
        selected = tuple(matches[:CANDIDATE_RESULT_LIMIT])
        return CandidateQueryOutcome(
            generated_at=now,
            status=(
                CandidateQueryStatus.OK
                if selected
                else CandidateQueryStatus.NO_CANDIDATES
            ),
            candidates=selected,
        )
