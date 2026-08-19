from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import ReservationTable, ResourceTargetTable
from app.domain.enums import Architecture, ReservationStatus
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
        idempotency_key: str,
        request_id: str,
        candidate_id: UUID,
        team_id: int,
        challenge_id: int,
        instance_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
        architecture: Architecture,
    ) -> ReservationCreateOutcome:
        now = datetime.now(UTC)
        existing = self._reservations.get_by_idempotency_key(
            idempotency_key
        )
        if existing is not None:
            self._expire_if_needed(existing, now)
            if not self._matches_request(
                existing,
                request_id=request_id,
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
            return ReservationCreateOutcome(existing, replayed=True)

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
                    candidate_id=candidate_id,
                    team_id=team_id,
                    challenge_id=challenge_id,
                    instance_id=instance_id,
                    cpu_millicores=cpu_millicores,
                    memory_mib=memory_mib,
                    ephemeral_storage_mib=ephemeral_storage_mib,
                    architecture=architecture,
                ):
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
        self._expire_if_needed(reservation, datetime.now(UTC))
        return reservation

    def commit(self, reservation_id: UUID) -> ReservationTable:
        reservation = self._reservations.get(
            reservation_id,
            for_update=True,
        )
        if reservation is None:
            raise ReservationNotFoundError
        now = datetime.now(UTC)
        if self._expire_if_needed(reservation, now):
            raise ReservationStateConflictError
        if reservation.status is ReservationStatus.COMMITTED:
            return reservation
        if reservation.status is not ReservationStatus.HELD:
            raise ReservationStateConflictError

        reservation.status = ReservationStatus.COMMITTED
        reservation.committed_at = now
        reservation.expires_at = None
        reservation.updated_at = now
        self._session.commit()
        self._session.refresh(reservation)
        return reservation

    def release(self, reservation_id: UUID) -> ReservationTable:
        reservation = self._reservations.get(
            reservation_id,
            for_update=True,
        )
        if reservation is None:
            raise ReservationNotFoundError
        now = datetime.now(UTC)
        if self._expire_if_needed(reservation, now):
            return reservation
        if reservation.status is ReservationStatus.RELEASED:
            return reservation
        if reservation.status not in {
            ReservationStatus.HELD,
            ReservationStatus.COMMITTED,
        }:
            raise ReservationStateConflictError

        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
        reservation.expires_at = None
        reservation.updated_at = now
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
            or resource.architecture is not architecture
            or resource.runtime_type is None
            or resource.target_id is None
            or resource.runtime_last_seen_at is None
            or resource.runtime_last_seen_at <= observed_after
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
        if (
            reservation.status is ReservationStatus.HELD
            and reservation.expires_at is not None
            and reservation.expires_at <= now
        ):
            reservation.status = ReservationStatus.EXPIRED
            reservation.updated_at = now
            self._session.commit()
            self._session.refresh(reservation)
            return True
        return False

    @staticmethod
    def _matches_request(
        reservation: ReservationTable,
        *,
        request_id: str,
        candidate_id: UUID,
        team_id: int,
        challenge_id: int,
        instance_id: str,
        cpu_millicores: int,
        memory_mib: int,
        ephemeral_storage_mib: int,
        architecture: Architecture,
    ) -> bool:
        return (
            reservation.request_id == request_id
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
