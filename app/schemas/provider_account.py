from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.domain.enums import (
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
)


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
GmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=11,
        max_length=255,
        pattern=r"^[^@\s]+@gmail\.com$",
        strict=True,
    ),
]
AwsAccountId = Annotated[
    str,
    StringConstraints(pattern=r"^\d{12}$", strict=True),
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
    role_arn: ProviderIdentifier
    regions: list[ProviderIdentifier] = Field(min_length=1)

    @field_validator("regions")
    @classmethod
    def require_unique_regions(cls, value: list[str]) -> list[str]:
        return _require_unique_values("regions", value)


class AzureAccountConfig(ApiSchema):
    client_id: ProviderIdentifier
    subscription_ids: list[ProviderIdentifier] = Field(min_length=1)

    @field_validator("subscription_ids")
    @classmethod
    def require_unique_subscriptions(cls, value: list[str]) -> list[str]:
        return _require_unique_values("subscription_ids", value)


class OciAccountConfig(ApiSchema):
    compartment_ids: list[ProviderIdentifier] = Field(min_length=1)
    regions: list[ProviderIdentifier] = Field(min_length=1)

    @field_validator("compartment_ids")
    @classmethod
    def require_unique_compartments(cls, value: list[str]) -> list[str]:
        return _require_unique_values("compartment_ids", value)

    @field_validator("regions")
    @classmethod
    def require_unique_regions(cls, value: list[str]) -> list[str]:
        return _require_unique_values("regions", value)


class NcpAccountConfig(ApiSchema):
    region_codes: list[ProviderIdentifier] = Field(min_length=1)

    @field_validator("region_codes")
    @classmethod
    def require_unique_region_codes(cls, value: list[str]) -> list[str]:
        return _require_unique_values("region_codes", value)


class ProviderAccountCreateBase(ApiSchema):
    external_account_id: ExternalAccountId
    display_name: NonEmptyString | None = None
    enabled: bool = True


class GcpAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.GCP]
    external_account_id: GmailAddress
    config: GcpAccountConfig

    @field_validator("external_account_id")
    @classmethod
    def normalize_gmail_address(cls, value: str) -> str:
        return value.lower()


class AwsAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.AWS]
    external_account_id: AwsAccountId
    config: AwsAccountConfig


class AzureAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.AZURE]
    config: AzureAccountConfig


class OciAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.OCI]
    config: OciAccountConfig


class NcpAccountCreateRequest(ProviderAccountCreateBase):
    provider: Literal[Provider.NCP]
    config: NcpAccountConfig


ProviderAccountCreateRequest = Annotated[
    GcpAccountCreateRequest
    | AwsAccountCreateRequest
    | AzureAccountCreateRequest
    | OciAccountCreateRequest
    | NcpAccountCreateRequest,
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


class OciAccountResponse(ProviderAccountResponseBase):
    provider: Literal[Provider.OCI]
    config: OciAccountConfig


class NcpAccountResponse(ProviderAccountResponseBase):
    provider: Literal[Provider.NCP]
    config: NcpAccountConfig


ProviderAccountResponse = Annotated[
    GcpAccountResponse
    | AwsAccountResponse
    | AzureAccountResponse
    | OciAccountResponse
    | NcpAccountResponse,
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
    internal_ip: str | None
    external_ip: str | None


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


class ErrorDetail(ApiSchema):
    code: str
    message: str


class ErrorResponse(ApiSchema):
    error: ErrorDetail
