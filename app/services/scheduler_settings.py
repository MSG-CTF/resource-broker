import os
from dataclasses import dataclass


class SchedulerConfigurationError(RuntimeError):
    pass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise SchedulerConfigurationError(
            f"{name} must be a positive integer"
        ) from error
    if value <= 0:
        raise SchedulerConfigurationError(
            f"{name} must be a positive integer"
        )
    return value


@dataclass(frozen=True, slots=True)
class SchedulerSettings:
    observation_stale_seconds: int
    candidate_validity_seconds: int
    reservation_ttl_seconds: int

    @classmethod
    def from_environment(cls) -> "SchedulerSettings":
        return cls(
            observation_stale_seconds=_positive_int(
                "SCHEDULER_OBSERVATION_STALE_SECONDS",
                600,
            ),
            candidate_validity_seconds=_positive_int(
                "SCHEDULER_CANDIDATE_VALIDITY_SECONDS",
                30,
            ),
            reservation_ttl_seconds=_positive_int(
                "SCHEDULER_RESERVATION_TTL_SECONDS",
                120,
            ),
        )
