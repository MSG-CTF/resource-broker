from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    Architecture,
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
)
from app.schemas.resource_target import ResourceCapacityResponse


NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
ExternalAccountId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        strict=True,
    ),
]
AwsAccountId = Annotated[
    str,
    StringConstraints(pattern=r"^\d{12}$", strict=True),
]
AwsRoleArn = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^arn:aws:iam::\d{12}:role/"
            r"[A-Za-z0-9+=,.@_/-]+$"
        ),
        min_length=32,
        max_length=2048,
        strict=True,
    ),
]
AwsRegion = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$",
        min_length=8,
        max_length=64,
        strict=True,
    ),
]
AzureUuid = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
        strict=True,
    ),
]
ProviderIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1024,
        strict=True,
    ),
]


def _require_unique_values(field_name: str, values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GcpAccountConfig(ApiSchema):
    project_ids: list[ProviderIdentifier] = Field(min_length=1)

    @field_validator("project_ids")
    @classmethod
    def require_unique_projects(cls, value: list[str]) -> list[str]:
        return _require_unique_values("project_ids", value)


class AwsAccountConfig(ApiSchema):
    role_arn: AwsRoleArn
    regions: list[AwsRegion] = Field(min_length=1)

    @field_validator("regions")
    @classmethod
    def require_unique_regions(cls, value: list[str]) -> list[str]:
        return _require_unique_values("regions", value)


class AzureAccountConfig(ApiSchema):
    client_id: AzureUuid
    subscription_ids: list[AzureUuid] = Field(min_length=1)

    @field_validator("client_id")
    @classmethod
    def normalize_client_id(cls, value: str) -> str:
        return value.lower()

    @field_validator("subscription_ids")
    @classmethod
    def require_unique_subscriptions(cls, value: list[str]) -> list[str]:
        normalized = [subscription_id.lower() for subscription_id in value]
        return _require_unique_values("subscription_ids", normalized)


class ProviderAccountCreateBase(ApiSchema):
    external_account_id: ExternalAccountId
    display_name: NonEmptyString | None = None
    enabled: bool = True


class GcpAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.GCP]
    external_account_id: ExternalAccountId
    config: GcpAccountConfig


class AwsAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.AWS]
    external_account_id: AwsAccountId
    config: AwsAccountConfig

    @model_validator(mode="after")
    def require_role_in_external_account(self) -> "AwsAccountCreateRequest":
        role_account_id = self.config.role_arn.split(":", maxsplit=5)[4]
        if role_account_id != self.external_account_id:
            raise ValueError(
                "config.role_arn must belong to external_account_id"
            )
        return self


class AzureAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.AZURE]
    external_account_id: AzureUuid
    config: AzureAccountConfig

    @field_validator("external_account_id")
    @classmethod
    def normalize_tenant_id(cls, value: str) -> str:
        return value.lower()


ProviderAccountCreateRequest = Annotated[
    GcpAccountCreateRequest
    | AwsAccountCreateRequest
    | AzureAccountCreateRequest,
    Field(discriminator="provider"),
]


class ProviderAccountResponseBase(ApiSchema):
    account_id: UUID
    external_account_id: str
    display_name: str | None
    auth_method: str
    enabled: bool
    credential_status: CredentialStatus
    permission_status: PermissionStatus
    provider_api_status: ProviderApiStatus
    last_verified_at: datetime | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GcpAccountResponse(ProviderAccountResponseBase):
    provider: Literal[Provider.GCP]
    config: GcpAccountConfig


class AwsAccountResponse(ProviderAccountResponseBase):
    provider: Literal[Provider.AWS]
    config: AwsAccountConfig


class AzureAccountResponse(ProviderAccountResponseBase):
    provider: Literal[Provider.AZURE]
    config: AzureAccountConfig


ProviderAccountResponse = Annotated[
    GcpAccountResponse
    | AwsAccountResponse
    | AzureAccountResponse,
    Field(discriminator="provider"),
]


class ProviderAccountListResponse(ApiSchema):
    total: int = Field(ge=0)
    items: list[ProviderAccountResponse]


class ProviderScopeResult(ApiSchema):
    scope_id: str
    success: bool
    vm_count: int = Field(ge=0)
    error_code: str | None = None
    message: str | None = None


class ProviderAccountVerifyResponse(ApiSchema):
    account_id: UUID
    success: bool
    credential_status: CredentialStatus
    permission_status: PermissionStatus
    provider_api_status: ProviderApiStatus
    verified_at: datetime
    scopes: list[ProviderScopeResult]


class ProviderResourceResponse(ApiSchema):
    resource_target_id: UUID
    scope_id: str
    instance_id: str
    name: str
    region: str
    zone: str | None
    status: str
    machine_type: str
    architecture: Architecture | None
    internal_ip: str | None
    external_ip: str | None
    provider_capacity: ResourceCapacityResponse
    allocatable_capacity: ResourceCapacityResponse


class ProviderAccountSyncResponse(ApiSchema):
    account_id: UUID
    success: bool
    synced_at: datetime
    discovered_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    retired_count: int = Field(ge=0)
    scopes: list[ProviderScopeResult]
    resources: list[ProviderResourceResponse]


class ProviderAccountDeleteResponse(ApiSchema):
    account_id: UUID
    deleted_resource_count: int = Field(ge=0)


class ErrorDetail(ApiSchema):
    code: str
    message: str


class ErrorResponse(ApiSchema):
    error: ErrorDetail
