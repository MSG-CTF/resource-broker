from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import ProviderInstanceSummary
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
from app.repositories.provider_accounts import (
    ProviderAccountRepository,
    ResourceSyncResult,
    ResourceTargetRepository,
)


GcpAdapterFactory = Callable[[str], GcpAdapter]


class ProviderAccountNotFoundError(LookupError):
    pass


class DuplicateProviderAccountError(ValueError):
    pass


class InvalidProviderAccountConfigError(ValueError):
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


class ProviderAccountService:
    def __init__(
        self,
        session: Session,
        gcp_adapter_factory: GcpAdapterFactory = GcpAdapter.from_adc,
    ) -> None:
        self._session = session
        self._accounts = ProviderAccountRepository(session)
        self._resources = ResourceTargetRepository(session)
        self._gcp_adapter_factory = gcp_adapter_factory
        self._verify_handlers = {
            Provider.GCP: self._verify_gcp,
        }
        self._sync_handlers = {
            Provider.GCP: self._sync_gcp,
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
        account = ProviderAccountTable(
            provider=provider,
            external_account_id=external_account_id,
            display_name=display_name,
            auth_method=(
                "ADC" if provider == Provider.GCP else "NOT_CONFIGURED"
            ),
            credential_reference=None,
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

    def _get_account(self, account_id: UUID) -> ProviderAccountTable:
        account = self._accounts.get(account_id)
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
        authentication_failed = GcpAuthenticationError.error_code in error_codes
        permission_failed = bool(
            {
                GcpPermissionError.error_code,
                GcpProjectNotFoundError.error_code,
            }
            & error_codes
        )
        api_failed = bool(
            {
                GcpAdapterError.error_code,
                GcpApiUnavailableError.error_code,
                GcpDependencyError.error_code,
            }
            & error_codes
        )

        account.credential_status = (
            CredentialStatus.INVALID
            if authentication_failed
            else CredentialStatus.VALID
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
