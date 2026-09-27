from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


EnrollmentToken = Annotated[
    str,
    StringConstraints(
        min_length=40,
        max_length=256,
        pattern=r"^mbe_[A-Za-z0-9_-]+$",
        strict=True,
    ),
]
CertificateSigningRequestPem = Annotated[
    str,
    StringConstraints(
        min_length=128,
        max_length=8192,
        strict=True,
    ),
]


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentEnrollmentTokenCreateRequest(ApiSchema):
    expires_in_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        strict=True,
    )


class AgentEnrollmentTokenResponse(ApiSchema):
    enrollment_id: UUID
    resource_target_id: UUID
    token: EnrollmentToken
    expires_at: datetime
    created_at: datetime


class AgentEnrollmentRequest(ApiSchema):
    resource_target_id: UUID
    certificate_signing_request_pem: CertificateSigningRequestPem
    aws_instance_identity_document: str | None = Field(
        default=None,
        min_length=64,
        max_length=4096,
    )
    aws_instance_identity_signature: str | None = Field(
        default=None,
        min_length=64,
        max_length=4096,
        pattern=r"^[A-Za-z0-9+/=]+$",
    )
    azure_attested_document: str | None = Field(
        default=None,
        min_length=64,
        max_length=65536,
    )


class AgentEnrollmentResponse(ApiSchema):
    resource_target_id: UUID
    client_certificate_pem: str
    client_ca_pem: str
    serial_number: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    issued_at: datetime
