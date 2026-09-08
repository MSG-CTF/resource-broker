from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


AdminId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]
AdminPassword = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=1024,
        strict=True,
    ),
]


class AdminAuthSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminLoginRequest(AdminAuthSchema):
    admin_id: AdminId
    password: AdminPassword


class AdminLoginResponse(AdminAuthSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)
