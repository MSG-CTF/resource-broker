from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AzureBrowserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_on: int = Field(strict=True, gt=0)


class AzureUserLoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["DISCONNECTED", "CONNECTED"]
    client_id: str | None
    tenant_id: str
    expires_at: datetime | None = None
    message: str | None = None
