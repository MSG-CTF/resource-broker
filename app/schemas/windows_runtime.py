from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowsVmConnectionUpdate(ApiSchema):
    username: str = Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")
    port: int = Field(default=3389, strict=True, ge=1, le=65535)
    address_source: Literal["public_ip", "private_ip"] = "public_ip"
    enabled: bool = Field(strict=True)
    reset_execution_id: UUID | None = None

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("The Windows username must not have surrounding whitespace.")
        return value


class WindowsVmConnectionResponse(ApiSchema):
    resource_target_id: UUID
    name: str | None
    protocol: Literal["rdp"] = "rdp"
    host: str
    port: int
    username: str
    rdp_file: str


class WindowsVmAvailableListResponse(ApiSchema):
    items: list[WindowsVmConnectionResponse]
    next_cursor: UUID | None


class WindowsVmConnectionAdminResponse(ApiSchema):
    resource_target_id: UUID
    username: str
    port: int
    address_source: Literal["public_ip", "private_ip"]
    enabled: bool
    state: Literal["READY", "RUNNING", "RESET_REQUIRED"]
    current_execution_id: UUID | None
    updated_at: datetime


class WindowsVmExecutionEvent(ApiSchema):
    execution_id: UUID
    event: Literal["started", "finished"]
    result: Literal["succeeded", "failed", "timeout", "unreachable"] | None = None
    exit_code: int | None = Field(default=None, strict=True, ge=-2147483648, le=4294967295)

    @model_validator(mode="after")
    def valid_event(self):
        if self.event == "started" and (self.result is not None or self.exit_code is not None):
            raise ValueError("A started event must not contain result or exit_code.")
        if self.event == "finished" and self.result is None:
            raise ValueError("A finished event requires result.")
        if self.result == "succeeded" and self.exit_code not in (None, 0):
            raise ValueError("A succeeded execution cannot have a nonzero exit_code.")
        return self


class WindowsVmExecutionResponse(ApiSchema):
    resource_target_id: UUID
    execution_id: UUID
    execution_state: Literal["RUNNING", "FINISHED"]
    vm_state: Literal["READY", "RUNNING", "RESET_REQUIRED"]
    started_at: datetime
    finished_at: datetime | None
    result: Literal["succeeded", "failed", "timeout", "unreachable"] | None
    exit_code: int | None
    replayed: bool
