import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.adapters.gcp_billing import GcpBillingAdapter
from app.db.tables import ProviderAccountTable
from app.domain.enums import Provider
from app.repositories.provider_accounts import ProviderAccountRepository


class GcpCreditAccountNotFoundError(LookupError):
    pass


class GcpCreditRequiresGcpAccountError(ValueError):
    pass


class GcpCreditTrackingNotConfiguredError(ValueError):
    pass


class InvalidGcpCreditTrackingConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GcpCreditTrackingSettings:
    billing_account_id: str
    billing_export_project_id: str
    billing_export_dataset_id: str
    baseline_remaining_credit: Decimal
    currency: str
    baseline_at: datetime


@dataclass(frozen=True, slots=True)
class GcpCreditEstimate:
    account_id: UUID
    currency: str
    estimated_remaining_credit: Decimal
    baseline_remaining_credit: Decimal
    promotional_credit_used: Decimal
    baseline_at: datetime
    data_through: datetime | None
    calculated_at: datetime


GcpBillingAdapterFactory = Callable[[str], GcpBillingAdapter]

_PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_BILLING_ACCOUNT_ID_PATTERN = re.compile(
    r"^[0-9A-Z]{6}-[0-9A-Z]{6}-[0-9A-Z]{6}$"
)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class GcpCreditService:
    def __init__(
        self,
        session: Session,
        billing_adapter_factory: GcpBillingAdapterFactory = (
            GcpBillingAdapter.from_adc
        ),
    ) -> None:
        self._session = session
        self._accounts = ProviderAccountRepository(session)
        self._billing_adapter_factory = billing_adapter_factory

    def update_settings(
        self,
        account_id: UUID,
        *,
        billing_account_id: str,
        billing_export_project_id: str,
        billing_export_dataset_id: str,
        baseline_remaining_credit: Decimal,
        currency: str,
    ) -> GcpCreditTrackingSettings:
        account = self._get_gcp_account(account_id, for_update=True)
        baseline_at = datetime.now(UTC)
        settings = GcpCreditTrackingSettings(
            billing_account_id=billing_account_id,
            billing_export_project_id=billing_export_project_id,
            billing_export_dataset_id=billing_export_dataset_id,
            baseline_remaining_credit=baseline_remaining_credit,
            currency=currency,
            baseline_at=baseline_at,
        )
        self._validate_settings(settings)

        provider_config = dict(account.provider_config)
        provider_config["credit_tracking"] = {
            "billing_account_id": settings.billing_account_id,
            "billing_export_project_id": (
                settings.billing_export_project_id
            ),
            "billing_export_dataset_id": (
                settings.billing_export_dataset_id
            ),
            "baseline_remaining_credit": format(
                settings.baseline_remaining_credit,
                "f",
            ),
            "currency": settings.currency,
            "baseline_at": settings.baseline_at.isoformat(),
        }
        account.provider_config = provider_config
        self._session.commit()
        self._session.refresh(account)
        return settings

    def estimate(self, account_id: UUID) -> GcpCreditEstimate:
        account = self._get_gcp_account(account_id)
        settings = self._settings_from_account(account)

        try:
            adapter = self._billing_adapter_factory(
                settings.billing_export_project_id
            )
            usage = adapter.promotional_credit_usage(
                billing_account_id=settings.billing_account_id,
                billing_export_dataset_id=(
                    settings.billing_export_dataset_id
                ),
                baseline_at=settings.baseline_at,
            )
        except ValueError as error:
            raise InvalidGcpCreditTrackingConfigError from error

        if usage.currency is not None and usage.currency != settings.currency:
            raise InvalidGcpCreditTrackingConfigError

        remaining = max(
            settings.baseline_remaining_credit
            - usage.promotional_credit_used,
            Decimal("0"),
        )
        return GcpCreditEstimate(
            account_id=account.account_id,
            currency=settings.currency,
            estimated_remaining_credit=remaining,
            baseline_remaining_credit=settings.baseline_remaining_credit,
            promotional_credit_used=usage.promotional_credit_used,
            baseline_at=settings.baseline_at,
            data_through=usage.data_through,
            calculated_at=datetime.now(UTC),
        )

    def _get_gcp_account(
        self,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> ProviderAccountTable:
        account = self._accounts.get(account_id, for_update=for_update)
        if account is None:
            raise GcpCreditAccountNotFoundError
        if account.provider is not Provider.GCP:
            raise GcpCreditRequiresGcpAccountError
        return account

    def _settings_from_account(
        self,
        account: ProviderAccountTable,
    ) -> GcpCreditTrackingSettings:
        raw = account.provider_config.get("credit_tracking")
        if raw is None:
            raise GcpCreditTrackingNotConfiguredError
        if not isinstance(raw, dict):
            raise InvalidGcpCreditTrackingConfigError

        try:
            settings = GcpCreditTrackingSettings(
                billing_account_id=raw["billing_account_id"],
                billing_export_project_id=raw[
                    "billing_export_project_id"
                ],
                billing_export_dataset_id=raw[
                    "billing_export_dataset_id"
                ],
                baseline_remaining_credit=Decimal(
                    str(raw["baseline_remaining_credit"])
                ),
                currency=raw["currency"],
                baseline_at=datetime.fromisoformat(raw["baseline_at"]),
            )
            self._validate_settings(settings)
        except (
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise InvalidGcpCreditTrackingConfigError from error
        return settings

    @staticmethod
    def _validate_settings(settings: GcpCreditTrackingSettings) -> None:
        valid = (
            isinstance(settings.billing_account_id, str)
            and bool(
                _BILLING_ACCOUNT_ID_PATTERN.fullmatch(
                    settings.billing_account_id
                )
            )
            and isinstance(settings.billing_export_project_id, str)
            and bool(
                _PROJECT_ID_PATTERN.fullmatch(
                    settings.billing_export_project_id
                )
            )
            and isinstance(settings.billing_export_dataset_id, str)
            and bool(
                _DATASET_ID_PATTERN.fullmatch(
                    settings.billing_export_dataset_id
                )
            )
            and isinstance(settings.currency, str)
            and bool(_CURRENCY_PATTERN.fullmatch(settings.currency))
            and isinstance(settings.baseline_remaining_credit, Decimal)
            and settings.baseline_remaining_credit.is_finite()
            and settings.baseline_remaining_credit >= 0
            and isinstance(settings.baseline_at, datetime)
            and settings.baseline_at.tzinfo is not None
        )
        if not valid:
            raise InvalidGcpCreditTrackingConfigError
