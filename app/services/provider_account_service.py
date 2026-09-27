import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import ProviderInstanceSummary
from app.adapters.aws import (
    AwsAccountMismatchError,
    AwsAdapter,
    AwsAdapterError,
    AwsApiUnavailableError,
    AwsAuthenticationError,
    AwsDependencyError,
    AwsFederation,
    AwsPermissionError,
    AwsRegionNotAccessibleError,
    AwsResourceLookupError,
    AwsTemporaryCredentials,
)
from app.adapters.azure import (
    AzureAdapter,
    AzureAdapterError,
    AzureApiUnavailableError,
    AzureAuthenticationError,
    AzureDependencyError,
    AzurePermissionError,
    AzureSubscriptionNotFoundError,
)
from app.adapters.gcp import (
    GcpAdapter,
    GcpAdapterError,
    GcpApiUnavailableError,
    GcpAuthenticationError,
    GcpDependencyError,
    GcpPermissionError,
    GcpProjectNotFoundError,
)
from app.db.tables import ProviderAccountTable, ResourceTargetTable
from app.domain.enums import (
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
)
from app.repositories.bootstrap_jobs import BootstrapJobRepository
from app.repositories.provider_accounts import (
    ProviderAccountRepository,
    ResourceSyncResult,
    ResourceTargetRepository,
)
from app.repositories.reservations import ReservationRepository


GcpAdapterFactory = Callable[[str], GcpAdapter]
AwsFederationFactory = Callable[[str, str, str], AwsFederation]
AwsAdapterFactory = Callable[[str, AwsTemporaryCredentials], AwsAdapter]
AzureAdapterFactory = Callable[[str, str, str], AzureAdapter]
AwsSettingProvider = Callable[[], str | None]


def _aws_hub_role_arn_from_environment() -> str | None:
    return os.getenv("AWS_FEDERATION_HUB_ROLE_ARN")


def _aws_federation_audience_from_environment() -> str | None:
    return os.getenv("AWS_FEDERATION_AUDIENCE")


def _aws_sts_region_from_environment() -> str | None:
    return os.getenv("AWS_STS_REGION", "us-east-1")


class ProviderAccountNotFoundError(LookupError):
    pass


class DuplicateProviderAccountError(ValueError):
    pass


class InvalidProviderAccountConfigError(ValueError):
    pass


class ProviderAccountHasActiveBootstrapJobsError(ValueError):
    pass


class ProviderAccountHasActiveReservationsError(ValueError):
    pass


class ProviderNotImplementedError(NotImplementedError):
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        super().__init__(f"The {provider.value} adapter is not implemented.")


@dataclass(frozen=True, slots=True)
class ProviderScopeInspection:
    provider_scope_id: str
    success: bool
    vm_count: int
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    account: ProviderAccountTable
    verified_at: datetime
    success: bool
    scopes: tuple[ProviderScopeInspection, ...]


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    account: ProviderAccountTable
    synced_at: datetime
    success: bool
    discovered_count: int
    created_count: int
    updated_count: int
    retired_count: int
    scopes: tuple[ProviderScopeInspection, ...]
    resources: tuple[ResourceTargetTable, ...]


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    account_id: UUID
    deleted_resource_count: int


class ProviderAccountService:
    def __init__(
        self,
        session: Session,
        gcp_adapter_factory: GcpAdapterFactory = GcpAdapter.from_adc,
        aws_federation_factory: AwsFederationFactory = (
            AwsFederation.from_google_oidc
        ),
        aws_adapter_factory: AwsAdapterFactory = AwsAdapter.from_credentials,
        aws_hub_role_arn_provider: AwsSettingProvider = (
            _aws_hub_role_arn_from_environment
        ),
        aws_federation_audience_provider: AwsSettingProvider = (
            _aws_federation_audience_from_environment
        ),
        aws_sts_region_provider: AwsSettingProvider = (
            _aws_sts_region_from_environment
        ),
        azure_adapter_factory: AzureAdapterFactory = (
            AzureAdapter.from_google_oidc
        ),
    ) -> None:
        self._session = session
        self._accounts = ProviderAccountRepository(session)
        self._resources = ResourceTargetRepository(session)
        self._bootstrap_jobs = BootstrapJobRepository(session)
        self._reservations = ReservationRepository(session)
        self._gcp_adapter_factory = gcp_adapter_factory
        self._aws_federation_factory = aws_federation_factory
        self._aws_adapter_factory = aws_adapter_factory
        self._aws_hub_role_arn_provider = aws_hub_role_arn_provider
        self._aws_federation_audience_provider = (
            aws_federation_audience_provider
        )
        self._aws_sts_region_provider = aws_sts_region_provider
        self._azure_adapter_factory = azure_adapter_factory
        self._verify_handlers = {
            Provider.GCP: self._verify_gcp,
            Provider.AWS: self._verify_aws,
            Provider.AZURE: self._verify_azure,
        }
        self._sync_handlers = {
            Provider.GCP: self._sync_gcp,
            Provider.AWS: self._sync_aws,
            Provider.AZURE: self._sync_azure,
        }

    def create_account(
        self,
        *,
        provider: Provider,
        external_account_id: str,
        display_name: str | None,
        provider_config: dict[str, Any],
        enabled: bool,
    ) -> ProviderAccountTable:
        auth_method = {
            Provider.GCP: "ADC",
            Provider.AWS: "GOOGLE_OIDC_STS",
            Provider.AZURE: "GOOGLE_OIDC_CLIENT_ASSERTION",
        }.get(provider, "NOT_CONFIGURED")
        credential_reference = {
            Provider.AWS: "env://AWS_FEDERATION_HUB_ROLE_ARN",
            Provider.AZURE: "federated://gcp-attached-service-account",
        }.get(provider)
        account = ProviderAccountTable(
            provider=provider,
            external_account_id=external_account_id,
            display_name=display_name,
            auth_method=auth_method,
            credential_reference=credential_reference,
            provider_config=provider_config,
            enabled=enabled,
        )
        try:
            self._accounts.add(account)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateProviderAccountError from error
        self._session.refresh(account)
        return account

    def list_accounts(self) -> tuple[ProviderAccountTable, ...]:
        return self._accounts.list_all()

    def verify(self, account_id: UUID) -> VerificationOutcome:
        account = self._get_account(account_id)
        handler = self._verify_handlers.get(account.provider)
        if handler is None:
            raise ProviderNotImplementedError(account.provider)
        return handler(account)

    def sync(self, account_id: UUID) -> SyncOutcome:
        account = self._get_account(account_id)
        handler = self._sync_handlers.get(account.provider)
        if handler is None:
            raise ProviderNotImplementedError(account.provider)
        return handler(account)

    def delete_account(self, account_id: UUID) -> DeleteOutcome:
        account = self._get_account(account_id, for_update=True)
        if self._bootstrap_jobs.has_active_for_account(account.account_id):
            raise ProviderAccountHasActiveBootstrapJobsError
        if self._reservations.has_active_for_account(account.account_id):
            raise ProviderAccountHasActiveReservationsError
        deleted_resource_count = self._resources.delete_by_account_id(
            account.account_id
        )
        self._accounts.delete(account)
        self._session.commit()
        return DeleteOutcome(
            account_id=account_id,
            deleted_resource_count=deleted_resource_count,
        )

    def _verify_gcp(
        self,
        account: ProviderAccountTable,
    ) -> VerificationOutcome:
        project_ids = self._project_ids(account)
        verified_at = datetime.now(UTC)
        inspections, _ = self._inspect_projects(project_ids)

        self._apply_account_status(account, inspections)
        account.last_verified_at = verified_at
        self._session.commit()
        self._session.refresh(account)

        return VerificationOutcome(
            account=account,
            verified_at=verified_at,
            success=all(item.success for item in inspections),
            scopes=inspections,
        )

    def _sync_gcp(self, account: ProviderAccountTable) -> SyncOutcome:
        project_ids = self._project_ids(account)
        synced_at = datetime.now(UTC)
        inspections, instances = self._inspect_projects(project_ids)
        success = all(item.success for item in inspections)

        self._apply_account_status(account, inspections)
        account.last_verified_at = synced_at

        if not success:
            self._session.commit()
            self._session.refresh(account)
            return SyncOutcome(
                account=account,
                synced_at=synced_at,
                success=False,
                discovered_count=len(instances),
                created_count=0,
                updated_count=0,
                retired_count=0,
                scopes=inspections,
                resources=(),
            )

        sync_result = self._resources.sync_instances(
            account_id=account.account_id,
            scope_ids=project_ids,
            instances=instances,
            observed_at=synced_at,
        )
        account.last_synced_at = synced_at
        self._session.commit()
        self._session.refresh(account)

        return self._successful_sync_outcome(
            account=account,
            synced_at=synced_at,
            inspections=inspections,
            instances=instances,
            sync_result=sync_result,
        )

    def _verify_azure(
        self,
        account: ProviderAccountTable,
    ) -> VerificationOutcome:
        subscription_ids = self._subscription_ids(account)
        verified_at = datetime.now(UTC)
        inspections, _ = self._inspect_subscriptions(
            account,
            subscription_ids,
        )

        self._apply_account_status(account, inspections)
        account.last_verified_at = verified_at
        self._session.commit()
        self._session.refresh(account)

        return VerificationOutcome(
            account=account,
            verified_at=verified_at,
            success=all(item.success for item in inspections),
            scopes=inspections,
        )

    def _verify_aws(
        self,
        account: ProviderAccountTable,
    ) -> VerificationOutcome:
        regions = self._aws_regions(account)
        verified_at = datetime.now(UTC)
        inspections, _ = self._inspect_aws_regions(account, regions)

        self._apply_account_status(account, inspections)
        account.last_verified_at = verified_at
        self._session.commit()
        self._session.refresh(account)

        return VerificationOutcome(
            account=account,
            verified_at=verified_at,
            success=all(item.success for item in inspections),
            scopes=inspections,
        )

    def _sync_aws(self, account: ProviderAccountTable) -> SyncOutcome:
        regions = self._aws_regions(account)
        synced_at = datetime.now(UTC)
        inspections, instances = self._inspect_aws_regions(account, regions)
        success = all(item.success for item in inspections)
        successful_regions = tuple(
            item.provider_scope_id for item in inspections if item.success
        )

        self._apply_account_status(account, inspections)
        account.last_verified_at = synced_at

        if not successful_regions:
            self._session.commit()
            self._session.refresh(account)
            return SyncOutcome(
                account=account,
                synced_at=synced_at,
                success=False,
                discovered_count=0,
                created_count=0,
                updated_count=0,
                retired_count=0,
                scopes=inspections,
                resources=(),
            )

        sync_result = self._resources.sync_instances(
            account_id=account.account_id,
            scope_ids=successful_regions,
            instances=instances,
            observed_at=synced_at,
        )
        if success:
            account.last_synced_at = synced_at
        self._session.commit()
        self._session.refresh(account)

        return SyncOutcome(
            account=account,
            synced_at=synced_at,
            success=success,
            discovered_count=len(instances),
            created_count=sync_result.created_count,
            updated_count=sync_result.updated_count,
            retired_count=sync_result.retired_count,
            scopes=inspections,
            resources=sync_result.resources,
        )

    def _sync_azure(self, account: ProviderAccountTable) -> SyncOutcome:
        subscription_ids = self._subscription_ids(account)
        synced_at = datetime.now(UTC)
        inspections, instances = self._inspect_subscriptions(
            account,
            subscription_ids,
        )
        success = all(item.success for item in inspections)

        self._apply_account_status(account, inspections)
        account.last_verified_at = synced_at

        if not success:
            self._session.commit()
            self._session.refresh(account)
            return SyncOutcome(
                account=account,
                synced_at=synced_at,
                success=False,
                discovered_count=len(instances),
                created_count=0,
                updated_count=0,
                retired_count=0,
                scopes=inspections,
                resources=(),
            )

        sync_result = self._resources.sync_instances(
            account_id=account.account_id,
            scope_ids=subscription_ids,
            instances=instances,
            observed_at=synced_at,
        )
        account.last_synced_at = synced_at
        self._session.commit()
        self._session.refresh(account)

        return self._successful_sync_outcome(
            account=account,
            synced_at=synced_at,
            inspections=inspections,
            instances=instances,
            sync_result=sync_result,
        )

    def _get_account(
        self,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> ProviderAccountTable:
        account = self._accounts.get(account_id, for_update=for_update)
        if account is None:
            raise ProviderAccountNotFoundError
        return account

    @staticmethod
    def _project_ids(account: ProviderAccountTable) -> tuple[str, ...]:
        raw_project_ids = account.provider_config.get("project_ids")
        if not isinstance(raw_project_ids, list) or not raw_project_ids:
            raise InvalidProviderAccountConfigError(
                "The provider account has no configured GCP projects."
            )
        if not all(
            isinstance(project_id, str)
            and bool(project_id)
            and project_id.strip() == project_id
            for project_id in raw_project_ids
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account contains an invalid GCP project ID."
            )
        return tuple(raw_project_ids)

    @staticmethod
    def _azure_client_id(account: ProviderAccountTable) -> str:
        client_id = account.provider_config.get("client_id")
        if (
            not isinstance(client_id, str)
            or not client_id
            or client_id.strip() != client_id
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account has no valid Azure client ID."
            )
        return client_id

    @staticmethod
    def _aws_role_arn(account: ProviderAccountTable) -> str:
        role_arn = account.provider_config.get("role_arn")
        expected_prefix = f"arn:aws:iam::{account.external_account_id}:role/"
        if (
            not isinstance(role_arn, str)
            or not role_arn.startswith(expected_prefix)
            or role_arn.strip() != role_arn
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account has no valid AWS spoke role ARN."
            )
        return role_arn

    @staticmethod
    def aws_external_id(account: ProviderAccountTable) -> str:
        configured = account.provider_config.get("external_id")
        if isinstance(configured, str) and configured.strip() == configured:
            if configured:
                return configured
        return f"mbe-{account.account_id}"

    @staticmethod
    def aws_bootstrap_role_arn(account: ProviderAccountTable) -> str:
        configured = account.provider_config.get("bootstrap_role_arn")
        expected_prefix = f"arn:aws:iam::{account.external_account_id}:role/"
        if isinstance(configured, str) and configured.startswith(expected_prefix):
            if configured.strip() == configured:
                return configured
        return (
            f"arn:aws:iam::{account.external_account_id}:role/"
            "MsgBrokerBootstrapRole"
        )

    @staticmethod
    def _aws_regions(account: ProviderAccountTable) -> tuple[str, ...]:
        raw_regions = account.provider_config.get("regions")
        if not isinstance(raw_regions, list) or not raw_regions:
            raise InvalidProviderAccountConfigError(
                "The provider account has no configured AWS Regions."
            )
        if not all(
            isinstance(region, str)
            and bool(region)
            and region.strip() == region
            for region in raw_regions
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account contains an invalid AWS Region."
            )
        if len(raw_regions) != len(set(raw_regions)):
            raise InvalidProviderAccountConfigError(
                "The provider account contains duplicate AWS Regions."
            )
        return tuple(raw_regions)

    @staticmethod
    def _subscription_ids(
        account: ProviderAccountTable,
    ) -> tuple[str, ...]:
        raw_subscription_ids = account.provider_config.get("subscription_ids")
        if (
            not isinstance(raw_subscription_ids, list)
            or not raw_subscription_ids
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account has no configured Azure subscriptions."
            )
        if not all(
            isinstance(subscription_id, str)
            and bool(subscription_id)
            and subscription_id.strip() == subscription_id
            for subscription_id in raw_subscription_ids
        ):
            raise InvalidProviderAccountConfigError(
                "The provider account contains an invalid Azure subscription ID."
            )
        return tuple(raw_subscription_ids)

    @staticmethod
    def _required_aws_setting(
        provider: AwsSettingProvider,
        message: str,
    ) -> str:
        value = provider()
        if not isinstance(value, str) or not value or value.strip() != value:
            raise InvalidProviderAccountConfigError(message)
        return value

    def _inspect_projects(
        self,
        project_ids: tuple[str, ...],
    ) -> tuple[
        tuple[ProviderScopeInspection, ...],
        tuple[ProviderInstanceSummary, ...],
    ]:
        inspections: list[ProviderScopeInspection] = []
        instances: list[ProviderInstanceSummary] = []

        for project_id in project_ids:
            try:
                project_instances = self._gcp_adapter_factory(
                    project_id
                ).list_instances()
            except GcpAdapterError as error:
                inspections.append(
                    ProviderScopeInspection(
                        provider_scope_id=project_id,
                        success=False,
                        vm_count=0,
                        error_code=error.error_code,
                        message=error.public_message,
                    )
                )
                continue

            instances.extend(project_instances)
            inspections.append(
                ProviderScopeInspection(
                    provider_scope_id=project_id,
                    success=True,
                    vm_count=len(project_instances),
                )
            )

        return tuple(inspections), tuple(instances)

    def _inspect_aws_regions(
        self,
        account: ProviderAccountTable,
        regions: tuple[str, ...],
    ) -> tuple[
        tuple[ProviderScopeInspection, ...],
        tuple[ProviderInstanceSummary, ...],
    ]:
        hub_role_arn = self._required_aws_setting(
            self._aws_hub_role_arn_provider,
            "The AWS federation Hub Role ARN is not configured.",
        )
        audience = self._required_aws_setting(
            self._aws_federation_audience_provider,
            "The AWS federation audience is not configured.",
        )
        sts_region = self._required_aws_setting(
            self._aws_sts_region_provider,
            "The AWS STS Region is not configured.",
        )
        role_arn = self._aws_role_arn(account)

        try:
            federation = self._aws_federation_factory(
                hub_role_arn,
                audience,
                sts_region,
            )
            credentials = federation.assume_spoke_role(
                expected_account_id=account.external_account_id,
                spoke_role_arn=role_arn,
                external_id=self.aws_external_id(account),
            )
        except AwsAdapterError as error:
            return (
                tuple(
                    ProviderScopeInspection(
                        provider_scope_id=region,
                        success=False,
                        vm_count=0,
                        error_code=error.error_code,
                        message=error.public_message,
                    )
                    for region in regions
                ),
                (),
            )

        inspections: list[ProviderScopeInspection] = []
        instances: list[ProviderInstanceSummary] = []
        for region in regions:
            try:
                region_instances = self._aws_adapter_factory(
                    region,
                    credentials,
                ).list_instances()
            except AwsAdapterError as error:
                inspections.append(
                    ProviderScopeInspection(
                        provider_scope_id=region,
                        success=False,
                        vm_count=0,
                        error_code=error.error_code,
                        message=error.public_message,
                    )
                )
                continue

            instances.extend(region_instances)
            inspections.append(
                ProviderScopeInspection(
                    provider_scope_id=region,
                    success=True,
                    vm_count=len(region_instances),
                )
            )

        return tuple(inspections), tuple(instances)

    def _inspect_subscriptions(
        self,
        account: ProviderAccountTable,
        subscription_ids: tuple[str, ...],
    ) -> tuple[
        tuple[ProviderScopeInspection, ...],
        tuple[ProviderInstanceSummary, ...],
    ]:
        tenant_id = account.external_account_id
        client_id = self._azure_client_id(account)
        account.auth_method = "GOOGLE_OIDC_CLIENT_ASSERTION"
        account.credential_reference = (
            "federated://gcp-attached-service-account"
        )
        inspections: list[ProviderScopeInspection] = []
        instances: list[ProviderInstanceSummary] = []

        for subscription_id in subscription_ids:
            try:
                subscription_instances = self._azure_adapter_factory(
                    tenant_id,
                    client_id,
                    subscription_id,
                ).list_instances()
            except AzureAdapterError as error:
                inspections.append(
                    ProviderScopeInspection(
                        provider_scope_id=subscription_id,
                        success=False,
                        vm_count=0,
                        error_code=error.error_code,
                        message=error.public_message,
                    )
                )
                continue

            instances.extend(subscription_instances)
            inspections.append(
                ProviderScopeInspection(
                    provider_scope_id=subscription_id,
                    success=True,
                    vm_count=len(subscription_instances),
                )
            )

        return tuple(inspections), tuple(instances)

    @staticmethod
    def _apply_account_status(
        account: ProviderAccountTable,
        inspections: tuple[ProviderScopeInspection, ...],
    ) -> None:
        failures = tuple(item for item in inspections if not item.success)
        if not failures:
            account.credential_status = CredentialStatus.VALID
            account.permission_status = PermissionStatus.SUFFICIENT
            account.provider_api_status = ProviderApiStatus.AVAILABLE
            return

        error_codes = {item.error_code for item in failures}
        authentication_failed = bool(
            {
                AwsAuthenticationError.error_code,
                GcpAuthenticationError.error_code,
                AzureAuthenticationError.error_code,
            }
            & error_codes
        )
        permission_failed = bool(
            {
                AwsPermissionError.error_code,
                AwsAccountMismatchError.error_code,
                AwsRegionNotAccessibleError.error_code,
                GcpPermissionError.error_code,
                GcpProjectNotFoundError.error_code,
                AzurePermissionError.error_code,
                AzureSubscriptionNotFoundError.error_code,
            }
            & error_codes
        )
        api_failed = bool(
            {
                AwsAdapterError.error_code,
                AwsApiUnavailableError.error_code,
                AwsDependencyError.error_code,
                AwsResourceLookupError.error_code,
                GcpAdapterError.error_code,
                GcpApiUnavailableError.error_code,
                GcpDependencyError.error_code,
                AzureAdapterError.error_code,
                AzureApiUnavailableError.error_code,
                AzureDependencyError.error_code,
            }
            & error_codes
        )

        account.credential_status = (
            CredentialStatus.INVALID
            if authentication_failed
            else (
                CredentialStatus.UNKNOWN
                if api_failed
                else CredentialStatus.VALID
            )
        )
        if permission_failed:
            account.permission_status = PermissionStatus.INSUFFICIENT
        else:
            account.permission_status = PermissionStatus.UNKNOWN
        if api_failed:
            account.provider_api_status = ProviderApiStatus.UNAVAILABLE
        elif authentication_failed:
            account.provider_api_status = ProviderApiStatus.UNKNOWN
        else:
            account.provider_api_status = ProviderApiStatus.AVAILABLE

    @staticmethod
    def _successful_sync_outcome(
        *,
        account: ProviderAccountTable,
        synced_at: datetime,
        inspections: tuple[ProviderScopeInspection, ...],
        instances: tuple[ProviderInstanceSummary, ...],
        sync_result: ResourceSyncResult,
    ) -> SyncOutcome:
        return SyncOutcome(
            account=account,
            synced_at=synced_at,
            success=True,
            discovered_count=len(instances),
            created_count=sync_result.created_count,
            updated_count=sync_result.updated_count,
            retired_count=sync_result.retired_count,
            scopes=inspections,
            resources=sync_result.resources,
        )
