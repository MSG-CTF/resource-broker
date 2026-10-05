import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


class BigQueryClient(Protocol):
    def query(self, query: str, *, job_config: object) -> Any:
        raise NotImplementedError


class GcpBillingAdapterError(RuntimeError):
    error_code = "GCP_BILLING_QUERY_FAILED"
    public_message = "The GCP billing export query failed."

    def __init__(self, billing_export_project_id: str) -> None:
        self.billing_export_project_id = billing_export_project_id
        super().__init__(self.public_message)


class GcpBillingDependencyError(GcpBillingAdapterError):
    error_code = "GCP_BIGQUERY_SDK_NOT_INSTALLED"
    public_message = "The GCP BigQuery SDK dependency is not installed."


class GcpBillingAuthenticationError(GcpBillingAdapterError):
    error_code = "GCP_BILLING_AUTHENTICATION_FAILED"
    public_message = "GCP authentication failed."


class GcpBillingPermissionError(GcpBillingAdapterError):
    error_code = "GCP_BILLING_PERMISSION_DENIED"
    public_message = (
        "The broker service account cannot query the billing export."
    )


class GcpBillingExportNotFoundError(GcpBillingAdapterError):
    error_code = "GCP_BILLING_EXPORT_NOT_FOUND"
    public_message = "The configured GCP billing export table was not found."


class GcpBillingUnavailableError(GcpBillingAdapterError):
    error_code = "GCP_BILLING_API_UNAVAILABLE"
    public_message = "The GCP BigQuery API is temporarily unavailable."


class GcpBillingQueryError(GcpBillingAdapterError):
    error_code = "GCP_BILLING_QUERY_INVALID"
    public_message = "The GCP billing export could not be queried."


@dataclass(frozen=True, slots=True)
class GcpPromotionalCreditUsage:
    promotional_credit_used: Decimal
    data_through: datetime | None
    currency: str | None


_PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_BILLING_ACCOUNT_ID_PATTERN = re.compile(
    r"^[0-9A-Z]{6}-[0-9A-Z]{6}-[0-9A-Z]{6}$"
)


def _translate_google_error(
    billing_export_project_id: str,
    error: Exception,
) -> GcpBillingAdapterError:
    try:
        from google.api_core import exceptions as api_exceptions
        from google.auth import exceptions as auth_exceptions
    except ModuleNotFoundError:
        return GcpBillingDependencyError(billing_export_project_id)

    if isinstance(error, auth_exceptions.GoogleAuthError):
        return GcpBillingAuthenticationError(billing_export_project_id)
    if isinstance(error, api_exceptions.Unauthenticated):
        return GcpBillingAuthenticationError(billing_export_project_id)
    if isinstance(error, api_exceptions.Forbidden):
        return GcpBillingPermissionError(billing_export_project_id)
    if isinstance(error, api_exceptions.NotFound):
        return GcpBillingExportNotFoundError(billing_export_project_id)
    if isinstance(error, api_exceptions.BadRequest):
        return GcpBillingQueryError(billing_export_project_id)
    if isinstance(
        error,
        (
            api_exceptions.BadGateway,
            api_exceptions.GatewayTimeout,
            api_exceptions.InternalServerError,
            api_exceptions.ServiceUnavailable,
            api_exceptions.TooManyRequests,
        ),
    ):
        return GcpBillingUnavailableError(billing_export_project_id)
    if isinstance(error, api_exceptions.GoogleAPICallError):
        return GcpBillingUnavailableError(billing_export_project_id)
    return GcpBillingAdapterError(billing_export_project_id)


class GcpBillingAdapter:
    def __init__(
        self,
        billing_export_project_id: str,
        client: BigQueryClient,
        bigquery_module: Any,
    ) -> None:
        if not _PROJECT_ID_PATTERN.fullmatch(billing_export_project_id):
            raise ValueError("billing_export_project_id is invalid")
        self._billing_export_project_id = billing_export_project_id
        self._client = client
        self._bigquery = bigquery_module

    @classmethod
    def from_adc(
        cls,
        billing_export_project_id: str,
    ) -> "GcpBillingAdapter":
        try:
            from google.cloud import bigquery
        except ModuleNotFoundError as error:
            raise GcpBillingDependencyError(
                billing_export_project_id
            ) from error

        try:
            return cls(
                billing_export_project_id=billing_export_project_id,
                client=bigquery.Client(project=billing_export_project_id),
                bigquery_module=bigquery,
            )
        except Exception as error:
            raise _translate_google_error(
                billing_export_project_id,
                error,
            ) from error

    def promotional_credit_usage(
        self,
        *,
        billing_account_id: str,
        billing_export_dataset_id: str,
        baseline_at: datetime,
    ) -> GcpPromotionalCreditUsage:
        if not _BILLING_ACCOUNT_ID_PATTERN.fullmatch(billing_account_id):
            raise ValueError("billing_account_id is invalid")
        if not _DATASET_ID_PATTERN.fullmatch(billing_export_dataset_id):
            raise ValueError("billing_export_dataset_id is invalid")
        if baseline_at.tzinfo is None:
            raise ValueError("baseline_at must be timezone-aware")

        table_suffix = billing_account_id.replace("-", "_")
        table_id = (
            f"{self._billing_export_project_id}."
            f"{billing_export_dataset_id}."
            f"gcp_billing_export_v1_{table_suffix}"
        )
        query = f"""
            SELECT
              COALESCE(
                -SUM(IF(credit.type = 'PROMOTION', credit.amount, 0)),
                0
              ) AS promotional_credit_used,
              MAX(export_time) AS data_through,
              ANY_VALUE(currency) AS currency
            FROM `{table_id}`
            LEFT JOIN UNNEST(credits) AS credit
            WHERE billing_account_id = @billing_account_id
              AND usage_start_time >= @baseline_at
        """
        try:
            job_config = self._bigquery.QueryJobConfig(
                query_parameters=[
                    self._bigquery.ScalarQueryParameter(
                        "billing_account_id",
                        "STRING",
                        billing_account_id,
                    ),
                    self._bigquery.ScalarQueryParameter(
                        "baseline_at",
                        "TIMESTAMP",
                        baseline_at,
                    ),
                ]
            )
            rows = self._client.query(query, job_config=job_config).result()
            row = next(iter(rows), None)
        except Exception as error:
            raise _translate_google_error(
                self._billing_export_project_id,
                error,
            ) from error

        if row is None:
            return GcpPromotionalCreditUsage(Decimal("0"), None, None)

        try:
            raw_usage = row["promotional_credit_used"]
            usage = Decimal(str(raw_usage or 0))
            data_through = row["data_through"]
            currency = row["currency"]
            if not usage.is_finite():
                raise ValueError("promotional_credit_used must be finite")
            if data_through is not None and not isinstance(
                data_through,
                datetime,
            ):
                raise TypeError("data_through must be a datetime")
            if data_through is not None and data_through.tzinfo is None:
                raise ValueError("data_through must be timezone-aware")
            if currency is not None and (
                not isinstance(currency, str)
                or not re.fullmatch(r"^[A-Z]{3}$", currency)
            ):
                raise ValueError("currency is invalid")
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise GcpBillingQueryError(
                self._billing_export_project_id
            ) from error

        return GcpPromotionalCreditUsage(
            promotional_credit_used=max(usage, Decimal("0")),
            data_through=data_through,
            currency=currency,
        )
