from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class K3sCredentialUploadRequest(ApiSchema):
    schema_version: Literal[1]
    bootstrap_job_id: UUID
    resource_target_id: UUID
    server_url: str = Field(min_length=1, max_length=128)
    kubeconfig_base64: str = Field(min_length=1, max_length=1_000_000)
    client_certificate_fingerprint_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    client_certificate_not_after: datetime
    generated_at: datetime

    @field_validator("client_certificate_not_after", "generated_at")
    @classmethod
    def require_utc_seconds(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must use UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond != 0:
            raise ValueError("timestamp must use whole seconds")
        return normalized


class K3sCredentialUploadResponse(ApiSchema):
    resource_target_id: UUID
    source_bootstrap_job_id: UUID
    status: Literal["stored"] = "stored"
    uploaded_at: datetime


class RuntimeK3sCredentialResponse(ApiSchema):
    schema_version: Literal[1] = 1
    resource_target_id: UUID
    source_bootstrap_job_id: UUID
    server_url: str
    kubeconfig_base64: str
    client_certificate_fingerprint_sha256: str
    client_certificate_not_after: datetime
    generated_at: datetime
    uploaded_at: datetime


class RuntimeK3sCredentialListResponse(ApiSchema):
    items: list[RuntimeK3sCredentialResponse]
    next_cursor: UUID | None = None
