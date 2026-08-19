from collections.abc import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import ProviderAccountTable, ResourceTargetTable
from app.domain.enums import Provider
from app.schemas.provider_account import (
    AwsAccountResponse,
    AzureAccountResponse,
    ErrorResponse,
    GcpAccountResponse,
    ProviderAccountCreateRequest,
    ProviderAccountDeleteResponse,
    ProviderAccountListResponse,
    ProviderAccountResponse,
    ProviderAccountSyncResponse,
    ProviderAccountVerifyResponse,
    ProviderResourceResponse,
    ProviderScopeResult,
)
from app.schemas.resource_target import ResourceCapacityResponse
from app.services.provider_account_service import (
    DuplicateProviderAccountError,
    InvalidProviderAccountConfigError,
    ProviderAccountNotFoundError,
    ProviderAccountHasActiveBootstrapJobsError,
    ProviderAccountHasActiveReservationsError,
    ProviderAccountService,
    ProviderNotImplementedError,
    ProviderScopeInspection,
    SyncOutcome,
    VerificationOutcome,
)


router = APIRouter(
    prefix="/v1/admin/provider-accounts",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error={"code": code, "message": message})
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


ACCOUNT_RESPONSE_MODELS = {
    Provider.GCP: GcpAccountResponse,
    Provider.AWS: AwsAccountResponse,
    Provider.AZURE: AzureAccountResponse,
}


def _account_response(account: ProviderAccountTable) -> ProviderAccountResponse:
    response_model = ACCOUNT_RESPONSE_MODELS[account.provider]
    config = dict(account.provider_config)
    if account.provider is Provider.AWS:
        config["external_id"] = ProviderAccountService.aws_external_id(account)
        config["bootstrap_role_arn"] = (
            ProviderAccountService.aws_bootstrap_role_arn(account)
        )
    return response_model(
        account_id=account.account_id,
        provider=account.provider,
        external_account_id=account.external_account_id,
        display_name=account.display_name,
        config=config,
        auth_method=account.auth_method,
        enabled=account.enabled,
        credential_status=account.credential_status,
        permission_status=account.permission_status,
        provider_api_status=account.provider_api_status,
        last_verified_at=account.last_verified_at,
        last_synced_at=account.last_synced_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _scope_results(
    inspections: Iterable[ProviderScopeInspection],
) -> list[ProviderScopeResult]:
    return [
        ProviderScopeResult(
            scope_id=item.provider_scope_id,
            success=item.success,
            vm_count=item.vm_count,
            error_code=item.error_code,
            message=item.message,
        )
        for item in inspections
    ]


def _verify_response(
    outcome: VerificationOutcome,
) -> ProviderAccountVerifyResponse:
    account = outcome.account
    return ProviderAccountVerifyResponse(
        account_id=account.account_id,
        success=outcome.success,
        credential_status=account.credential_status,
        permission_status=account.permission_status,
        provider_api_status=account.provider_api_status,
        verified_at=outcome.verified_at,
        scopes=_scope_results(outcome.scopes),
    )


def _resource_response(
    resource: ResourceTargetTable,
) -> ProviderResourceResponse:
    allocatable = ResourceCapacityResponse(
        cpu_millicores=resource.allocatable_cpu_millicores,
        memory_mib=resource.allocatable_memory_mib,
        storage_mib=resource.allocatable_ephemeral_storage_mib,
    )
    return ProviderResourceResponse(
        resource_target_id=resource.resource_target_id,
        scope_id=resource.provider_scope_id or "",
        instance_id=resource.provider_instance_id,
        name=resource.instance_name or "",
        region=resource.region,
        zone=resource.zone,
        status=resource.provider_instance_state or "",
        machine_type=resource.provider_machine_type or "",
        architecture=resource.architecture,
        internal_ip=resource.private_ip,
        external_ip=resource.public_ip,
        provider_capacity=ResourceCapacityResponse(
            cpu_millicores=resource.provider_capacity_cpu_millicores,
            memory_mib=resource.provider_capacity_memory_mib,
            storage_mib=resource.provider_capacity_storage_mib,
        ),
        allocatable_capacity=allocatable,
    )


def _sync_response(outcome: SyncOutcome) -> ProviderAccountSyncResponse:
    return ProviderAccountSyncResponse(
        account_id=outcome.account.account_id,
        success=outcome.success,
        synced_at=outcome.synced_at,
        discovered_count=outcome.discovered_count,
        created_count=outcome.created_count,
        updated_count=outcome.updated_count,
        retired_count=outcome.retired_count,
        scopes=_scope_results(outcome.scopes),
        resources=[_resource_response(item) for item in outcome.resources],
    )


COMMON_ERROR_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_501_NOT_IMPLEMENTED: {"model": ErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=ProviderAccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def create_provider_account(
    request: ProviderAccountCreateRequest,
    session: Session = Depends(get_db_session),
) -> ProviderAccountResponse | Response:
    service = ProviderAccountService(session)
    try:
        account = service.create_account(
            provider=request.provider,
            external_account_id=request.external_account_id,
            display_name=request.display_name,
            provider_config=request.config.model_dump(mode="json"),
            enabled=request.enabled,
        )
    except DuplicateProviderAccountError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "PROVIDER_ACCOUNT_ALREADY_EXISTS",
            "This provider account is already registered.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The provider account could not be saved.",
        )
    return _account_response(account)


@router.get(
    "",
    response_model=ProviderAccountListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def list_provider_accounts(
    session: Session = Depends(get_db_session),
) -> ProviderAccountListResponse | Response:
    try:
        accounts = ProviderAccountService(session).list_accounts()
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The provider account list could not be loaded.",
        )
    items = [_account_response(account) for account in accounts]
    return ProviderAccountListResponse(total=len(items), items=items)


@router.post(
    "/{account_id}/verify",
    response_model=ProviderAccountVerifyResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def verify_provider_account(
    account_id: UUID,
    session: Session = Depends(get_db_session),
) -> ProviderAccountVerifyResponse | Response:
    try:
        outcome = ProviderAccountService(session).verify(account_id)
    except ProviderAccountNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "PROVIDER_ACCOUNT_NOT_FOUND",
            "The provider account was not found.",
        )
    except ProviderNotImplementedError as error:
        return _error_response(
            status.HTTP_501_NOT_IMPLEMENTED,
            "PROVIDER_NOT_IMPLEMENTED",
            str(error),
        )
    except InvalidProviderAccountConfigError as error:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_PROVIDER_ACCOUNT_CONFIG",
            str(error),
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The verification result could not be saved.",
        )
    return _verify_response(outcome)


@router.post(
    "/{account_id}/sync",
    response_model=ProviderAccountSyncResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        status.HTTP_502_BAD_GATEWAY: {
            "model": ProviderAccountSyncResponse,
            "description": "At least one configured provider scope could not be read.",
        },
    },
)
def sync_provider_account(
    account_id: UUID,
    session: Session = Depends(get_db_session),
) -> ProviderAccountSyncResponse | Response:
    try:
        outcome = ProviderAccountService(session).sync(account_id)
    except ProviderAccountNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "PROVIDER_ACCOUNT_NOT_FOUND",
            "The provider account was not found.",
        )
    except ProviderNotImplementedError as error:
        return _error_response(
            status.HTTP_501_NOT_IMPLEMENTED,
            "PROVIDER_NOT_IMPLEMENTED",
            str(error),
        )
    except InvalidProviderAccountConfigError as error:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_PROVIDER_ACCOUNT_CONFIG",
            str(error),
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The synchronization result could not be saved.",
        )

    response = _sync_response(outcome)
    if not outcome.success:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=response.model_dump(mode="json"),
        )
    return response


@router.delete(
    "/{account_id}",
    response_model=ProviderAccountDeleteResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def delete_provider_account(
    account_id: UUID,
    session: Session = Depends(get_db_session),
) -> ProviderAccountDeleteResponse | Response:
    try:
        outcome = ProviderAccountService(session).delete_account(account_id)
    except ProviderAccountNotFoundError:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "PROVIDER_ACCOUNT_NOT_FOUND",
            "The provider account was not found.",
        )
    except ProviderAccountHasActiveBootstrapJobsError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "PROVIDER_ACCOUNT_HAS_ACTIVE_BOOTSTRAP_JOBS",
            "The provider account has active bootstrap jobs.",
        )
    except ProviderAccountHasActiveReservationsError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "PROVIDER_ACCOUNT_HAS_ACTIVE_RESERVATIONS",
            "The provider account has active Scheduler reservations.",
        )
    except SQLAlchemyError:
        session.rollback()
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DATABASE_ERROR",
            "The provider account could not be deleted.",
        )

    return ProviderAccountDeleteResponse(
        account_id=outcome.account_id,
        deleted_resource_count=outcome.deleted_resource_count,
    )
