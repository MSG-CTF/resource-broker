from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import BootstrapAction, BootstrapJobStatus, Provider


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapJobCreateRequest(ApiSchema):
    action: BootstrapAction
    bootstrap_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    k3s_version: str | None = Field(
        default=None,
        pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$",
    )
    agent_image: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$",
        max_length=512,
    )

    @model_validator(mode="after")
    def validate_install_settings(self) -> "BootstrapJobCreateRequest":
        if self.action in {BootstrapAction.INSTALL, BootstrapAction.UPDATE}:
            if self.k3s_version is None or self.agent_image is None:
                raise ValueError(
                    "k3s_version and agent_image are required for install/update"
                )
        return self


class BootstrapJobResponse(ApiSchema):
    job_id: UUID
    resource_target_id: UUID
    provider: Provider
    action: BootstrapAction
    status: BootstrapJobStatus
    bootstrap_version: str
    k3s_version: str | None
    agent_image: str | None
    provider_job_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deadline_at: datetime


class BootstrapJobListResponse(ApiSchema):
    items: list[BootstrapJobResponse]
