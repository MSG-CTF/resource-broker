from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.domain.enums import WindowsWorkerLeaseStatus, WindowsWorkerStatus


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


GcpProjectId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$",
        strict=True,
    ),
]
GcpZone = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z]+(?:-[a-z0-9]+)+-[a-z]$",
        max_length=128,
        strict=True,
    ),
]
GcpResourceName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$",
        max_length=63,
        strict=True,
    ),
]
DisplayName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]


class WindowsWorkerPoolCreateRequest(ApiSchema):
    project_id: GcpProjectId
    zone: GcpZone
    managed_instance_group: GcpResourceName
    display_name: DisplayName | None = None


class WindowsWorkerResponse(ApiSchema):
    worker_id: UUID
    instance_name: str
    instance_id: str | None
    private_ip: str | None
    status: WindowsWorkerStatus
    ready_at: datetime | None
    last_seen_at: datetime | None
    error_code: str | None
    error_message: str | None


class WindowsWorkerPoolResponse(ApiSchema):
    pool_id: UUID
    project_id: str
    zone: str
    managed_instance_group: str
    display_name: str | None
    enabled: bool
    last_synced_at: datetime | None
    error_code: str | None
    error_message: str | None
    workers: list[WindowsWorkerResponse]
    created_at: datetime
    updated_at: datetime


class WindowsWorkerPoolListResponse(ApiSchema):
    items: list[WindowsWorkerPoolResponse]


class LeasedWindowsWorkerResponse(ApiSchema):
    worker_id: UUID
    address: str


class WindowsWorkerLeaseResponse(ApiSchema):
    lease_id: UUID
    status: WindowsWorkerLeaseStatus
    expires_at: datetime
    worker: LeasedWindowsWorkerResponse


class WindowsWorkerLeaseCompletionResponse(ApiSchema):
    lease_id: UUID
    status: WindowsWorkerLeaseStatus
