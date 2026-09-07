from enum import StrEnum


class Provider(StrEnum):
    AWS = "AWS"
    GCP = "GCP"
    AZURE = "AZURE"


class BootstrapAction(StrEnum):
    INSTALL = "INSTALL"
    UPDATE = "UPDATE"
    CHECK = "CHECK"
    REMOVE = "REMOVE"


class BootstrapJobStatus(StrEnum):
    QUEUED = "QUEUED"
    APPLYING = "APPLYING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Architecture(StrEnum):
    AMD64 = "AMD64"
    ARM64 = "ARM64"


class RuntimeType(StrEnum):
    KUBERNETES = "KUBERNETES"


class CandidateQueryStatus(StrEnum):
    OK = "OK"
    NO_CANDIDATES = "NO_CANDIDATES"


class ReservationStatus(StrEnum):
    HELD = "HELD"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ReleaseReason(StrEnum):
    RUNTIME_CREATE_FAILED = "RUNTIME_CREATE_FAILED"
    DEPLOYED_SPEC_MISMATCH = "DEPLOYED_SPEC_MISMATCH"
    SCHEDULER_CANCELLED = "SCHEDULER_CANCELLED"


class CredentialStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class PermissionStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class ProviderApiStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class QuotaStatus(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    EXCEEDED = "EXCEEDED"
    UNKNOWN = "UNKNOWN"
