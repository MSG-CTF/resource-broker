from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import ReservationTable, ResourceTargetTable
from app.domain.enums import Architecture, ReleaseReason, ReservationStatus
from app.domain.observation import OBSERVATION_CLOCK_SKEW
from app.domain.provider_state import RUNNING_PROVIDER_INSTANCE_STATE
from app.repositories.reservations import ReservationRepository
from app.services.scheduler_settings import SchedulerSettings


class ReservationNotFoundError(LookupError):
    pass


class CandidateNotFoundError(LookupError):
    pass


class CandidateUnavailableError(ValueError):
    pass


class InsufficientCandidateCapacityError(ValueError):
    pass


class ReservationIdempotencyConflictError(ValueError):
    pass


class ActiveInstanceReservationError(ValueError):
    pass


class ReservationStateConflictError(ValueError):
    pass


class ReservationInstanceMismatchError(ValueError):
    pass


class ReservationMutationConflictError(ValueError):
    pass


class DeployedSpecMismatchError(ValueError):
    pass


class ReservationConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReservationCreateOutcome:
    reservation: ReservationTable
    replayed: bool


class ReservationService:
    def __init__(
        self,
        session: Session,
        settings: SchedulerSettings | None = None,
    ) -> None:
        self._session = session
        self._reservations = ReservationRepository(session)
        self._settings = settings or SchedulerSettings.from_environment()

    def create(
        self,
        *,
        request_id: str,
        requested_at: datetime,
        candidate_id: UUID,
        team_id: UUID,
        challenge_id: UUID,
        instance_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
        architecture: Architecture,
    ) -> ReservationCreateOutcome:
        idempotency_key = request_id
        existing = self._reservations.get_by_idempotency_key(
            idempotency_key
        )
        if existing is not None:
            if not self._matches_request(
                existing,
                request_id=request_id,
                requested_at=requested_at,
                candidate_id=candidate_id,
                team_id=team_id,
                challenge_id=challenge_id,
                instance_id=instance_id,
                cpu_millicores=cpu_millicores,
                memory_mib=memory_mib,
                ephemeral_storage_mib=ephemeral_storage_mib,
                architecture=architecture,
            ):
                raise ReservationIdempotencyConflictError
            if self._expire_if_needed(existing, datetime.now(UTC)):
                self._session.commit()
            return ReservationCreateOutcome(existing, replayed=True)

        resource = self._reservations.get_resource_for_update(candidate_id)
        if resource is None:
            raise CandidateNotFoundError

        existing = self._reservations.get_by_idempotency_key(
            idempotency_key
        )
        if existing is not None:
            if not self._matches_request(
                existing,
                request_id=request_id,
                requested_at=requested_at,
                candidate_id=candidate_id,
                team_id=team_id,
                challenge_id=challenge_id,
                instance_id=instance_id,
                cpu_millicores=cpu_millicores,
                memory_mib=memory_mib,
                ephemeral_storage_mib=ephemeral_storage_mib,
                architecture=architecture,
            ):
                raise ReservationIdempotencyConflictError
            if self._expire_if_needed(existing, datetime.now(UTC)):
                self._session.commit()
            return ReservationCreateOutcome(existing, replayed=True)

        # Recheck capacity and expiry after waiting for the VM row lock.
        now = datetime.now(UTC)
        self._reservations.expire_held_for_target(
            resource_target_id=candidate_id,
            now=now,
        )
        self._reservations.expire_held_for_instance(
            team_id=team_id,
            instance_id=instance_id,
            now=now,
        )
        if self._reservations.get_active_for_instance(
            team_id=team_id,
            instance_id=instance_id,
        ) is not None:
            raise ActiveInstanceReservationError

        # Expiring older holds can also wait for a concurrent mutation.
        now = datetime.now(UTC)
        self._validate_resource(
            resource,
            architecture=architecture,
            now=now,
        )
        reserved = self._reservations.deductible_usage_for_target(
            resource_target_id=candidate_id,
            now=now,
        )
        if (
            (resource.allocatable_cpu_millicores or 0)
            - reserved.cpu_millicores
            < cpu_millicores
            or (resource.allocatable_memory_mib or 0)
            - reserved.memory_mib
            < memory_mib
            or (resource.allocatable_ephemeral_storage_mib or 0)
            - reserved.ephemeral_storage_mib
            < ephemeral_storage_mib
        ):
            raise InsufficientCandidateCapacityError

        reservation = ReservationTable(
            idempotency_key=idempotency_key,
            request_id=request_id,
            requested_at=requested_at,
            resource_target=resource,
            team_id=team_id,
            challenge_id=challenge_id,
            instance_id=instance_id,
            cpu_millicores=cpu_millicores,
            memory_mib=memory_mib,
            ephemeral_storage_mib=ephemeral_storage_mib,
            architecture=architecture,
            status=ReservationStatus.HELD,
            expires_at=now
            + timedelta(seconds=self._settings.reservation_ttl_seconds),
        )
        try:
            self._reservations.add(reservation)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            replay = self._reservations.get_by_idempotency_key(
                idempotency_key
            )
            if replay is not None:
                if self._matches_request(
                    replay,
                    request_id=request_id,
                    requested_at=requested_at,
                    candidate_id=candidate_id,
                    team_id=team_id,
                    challenge_id=challenge_id,
                    instance_id=instance_id,
                    cpu_millicores=cpu_millicores,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=ephemeral_storage_mib,
                    architecture=architecture,
                ):
                    if self._expire_if_needed(replay, datetime.now(UTC)):
                        self._session.commit()
                    return ReservationCreateOutcome(replay, replayed=True)
                raise ReservationIdempotencyConflictError from error
            if self._reservations.get_active_for_instance(
                team_id=team_id,
                instance_id=instance_id,
            ) is not None:
                raise ActiveInstanceReservationError from error
            raise ReservationConflictError from error

        self._session.refresh(reservation)
        return ReservationCreateOutcome(reservation, replayed=False)

    def get(self, reservation_id: UUID) -> ReservationTable:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise ReservationNotFoundError
        if self._expire_if_needed(reservation, datetime.now(UTC)):
            self._session.commit()
        return reservation

    def commit(
        self,
        reservation_id: UUID,
        *,
        request_id: str,
        requested_at: datetime,
        instance_id: str,
        runtime_workload_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
    ) -> ReservationTable:
        reservation = self._reservations.get(
            reservation_id,
            for_update=True,
        )
        if reservation is None:
            raise ReservationNotFoundError
        if reservation.instance_id != instance_id:
            raise ReservationInstanceMismatchError
        if reservation.commit_request_id == request_id:
            if self._matches_commit_request(
                reservation,
                request_id=request_id,
                requested_at=requested_at,
                runtime_workload_id=runtime_workload_id,
                cpu_millicores=cpu_millicores,
                memory_mib=memory_mib,
                ephemeral_storage_mib=ephemeral_storage_mib,
            ):
                return reservation
            raise ReservationMutationConflictError
        now = datetime.now(UTC)
        if self._expire_if_needed(reservation, now):
            self._session.commit()
            raise ReservationStateConflictError
        if reservation.status is not ReservationStatus.HELD:
            raise ReservationStateConflictError
        if (
            reservation.cpu_millicores != cpu_millicores
            or reservation.memory_mib != memory_mib
            or reservation.ephemeral_storage_mib
            != ephemeral_storage_mib
        ):
            raise DeployedSpecMismatchError

        reservation.status = ReservationStatus.COMMITTED
        reservation.committed_at = now
        reservation.commit_request_id = request_id
        reservation.commit_requested_at = requested_at
        reservation.runtime_workload_id = runtime_workload_id
        reservation.deployed_cpu_millicores = cpu_millicores
        reservation.deployed_memory_mib = memory_mib
        reservation.deployed_ephemeral_storage_mib = (
            ephemeral_storage_mib
        )
        reservation.expires_at = None
        reservation.updated_at = now
        self._session.commit()
        self._session.refresh(reservation)
        return reservation

    def release(
        self,
        reservation_id: UUID,
        *,
        request_id: str,
        requested_at: datetime,
        instance_id: str,
        release_reason: ReleaseReason,
    ) -> ReservationTable:
        reservation = self._reservations.get(
            reservation_id,
            for_update=True,
        )
        if reservation is None:
            raise ReservationNotFoundError
        if reservation.instance_id != instance_id:
            raise ReservationInstanceMismatchError
        if reservation.release_request_id == request_id:
            if self._matches_release_request(
                reservation,
                request_id=request_id,
                requested_at=requested_at,
                release_reason=release_reason,
            ):
                return reservation
            raise ReservationMutationConflictError
        if reservation.release_request_id is not None:
            raise ReservationStateConflictError
        now = datetime.now(UTC)
        self._expire_if_needed(reservation, now)
        if reservation.status is ReservationStatus.EXPIRED:
            self._record_release_request(
                reservation,
                request_id=request_id,
                requested_at=requested_at,
                release_reason=release_reason,
                now=now,
            )
            self._session.commit()
            self._session.refresh(reservation)
            return reservation
        if reservation.status is ReservationStatus.RELEASED:
            self._record_release_request(
                reservation,
                request_id=request_id,
                requested_at=requested_at,
                release_reason=release_reason,
                now=now,
            )
            self._session.commit()
            self._session.refresh(reservation)
            return reservation
        if reservation.status not in {
            ReservationStatus.HELD,
            ReservationStatus.COMMITTED,
        }:
            raise ReservationStateConflictError

        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
        self._record_release_request(
            reservation,
            request_id=request_id,
            requested_at=requested_at,
            release_reason=release_reason,
            now=now,
        )
        reservation.expires_at = None
        self._session.commit()
        self._session.refresh(reservation)
        return reservation

    def _validate_resource(
        self,
        resource: ResourceTargetTable,
        *,
        architecture: Architecture,
        now: datetime,
    ) -> None:
        observed_after = now - timedelta(
            seconds=self._settings.observation_stale_seconds
        )
        if (
            not resource.account.enabled
            or not resource.enabled
            or not resource.ready
            or resource.retired_at is not None
            or resource.provider_instance_state != RUNNING_PROVIDER_INSTANCE_STATE
            or resource.architecture is not architecture
            or resource.runtime_type is None
            or resource.target_id is None
            or resource.runtime_last_seen_at is None
            or resource.runtime_last_seen_at <= observed_after
            or resource.runtime_observed_at is None
            or resource.runtime_observed_at <= observed_after
            or resource.runtime_observed_at > now + OBSERVATION_CLOCK_SKEW
            or resource.provider_capacity_cpu_millicores is None
            or resource.provider_capacity_memory_mib is None
            or resource.runtime_cpu_usage_millicores is None
            or resource.runtime_memory_usage_mib is None
            or resource.allocatable_cpu_millicores is None
            or resource.allocatable_memory_mib is None
            or resource.allocatable_ephemeral_storage_mib is None
        ):
            raise CandidateUnavailableError

    def _expire_if_needed(
        self,
        reservation: ReservationTable,
        now: datetime,
    ) -> bool:
        expired = self._reservations.expire_held(
            reservation.reservation_id,
            now=now,
        )
        # Refresh even on a no-op: a concurrent commit/release may have won.
        # The caller commits so mutation locks stay held while recording
        # associated request metadata.
        self._session.refresh(reservation)
        return expired

    @staticmethod
    def _matches_request(
        reservation: ReservationTable,
        *,
        request_id: str,
        requested_at: datetime,
        candidate_id: UUID,
        team_id: UUID,
        challenge_id: UUID,
        instance_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
        architecture: Architecture,
    ) -> bool:
        return (
            reservation.request_id == request_id
            and reservation.requested_at == requested_at
            and reservation.resource_target_id == candidate_id
            and reservation.team_id == team_id
            and reservation.challenge_id == challenge_id
            and reservation.instance_id == instance_id
            and reservation.cpu_millicores == cpu_millicores
            and reservation.memory_mib == memory_mib
            and reservation.ephemeral_storage_mib
            == ephemeral_storage_mib
            and reservation.architecture is architecture
        )

    @staticmethod
    def _matches_commit_request(
        reservation: ReservationTable,
        *,
        request_id: str,
        requested_at: datetime,
        runtime_workload_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
    ) -> bool:
        return (
            reservation.commit_request_id == request_id
            and reservation.commit_requested_at == requested_at
            and reservation.runtime_workload_id == runtime_workload_id
            and reservation.deployed_cpu_millicores == cpu_millicores
            and reservation.deployed_memory_mib == memory_mib
            and reservation.deployed_ephemeral_storage_mib
            == ephemeral_storage_mib
        )

    @staticmethod
    def _matches_release_request(
        reservation: ReservationTable,
        *,
        request_id: str,
        requested_at: datetime,
        release_reason: ReleaseReason,
    ) -> bool:
        return (
            reservation.release_request_id == request_id
            and reservation.release_requested_at == requested_at
            and reservation.release_reason is release_reason
        )

    @staticmethod
    def _record_release_request(
        reservation: ReservationTable,
        *,
        request_id: str,
        requested_at: datetime,
        release_reason: ReleaseReason,
        now: datetime,
    ) -> None:
        reservation.release_request_id = request_id
        reservation.release_requested_at = requested_at
        reservation.release_reason = release_reason
        reservation.updated_at = now
