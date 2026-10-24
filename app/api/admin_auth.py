from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.admin_auth import AdminLoginRequest, AdminLoginResponse
from app.services.admin_auth_service import (
    AdminAuthConfigurationError,
    AdminAuthService,
    AdminPrincipal,
    InvalidAdminCredentialsError,
    InvalidAdminTokenError,
)


router = APIRouter(
    prefix="/v1/admin/auth",
    tags=["admin-auth"],
)
admin_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AdminAuthHttpError(Exception):
    status_code: int
    code: str
    message: str
    include_authenticate_header: bool = False


def admin_auth_http_error_handler(
    request: Request,
    error: AdminAuthHttpError,
) -> JSONResponse:
    del request
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if error.include_authenticate_header
        else None
    )
    return JSONResponse(
        status_code=error.status_code,
        headers=headers,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
            }
        },
    )


def _configuration_error() -> AdminAuthHttpError:
    return AdminAuthHttpError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="ADMIN_AUTH_NOT_CONFIGURED",
        message="Administrator authentication is not configured.",
    )


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Security(admin_bearer),
) -> AdminPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AdminAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="ADMIN_TOKEN_REQUIRED",
            message="A valid administrator Bearer token is required.",
            include_authenticate_header=True,
        )

    try:
        return AdminAuthService().authenticate(credentials.credentials)
    except AdminAuthConfigurationError as error:
        raise _configuration_error() from error
    except InvalidAdminTokenError as error:
        raise AdminAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_ADMIN_TOKEN",
            message="The administrator token is invalid or expired.",
            include_authenticate_header=True,
        ) from error


@router.post(
    "/login",
    response_model=AdminLoginResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The administrator credentials are invalid.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Administrator authentication is not configured.",
        },
    },
)
def login_admin(request: AdminLoginRequest) -> AdminLoginResponse:
    try:
        token = AdminAuthService().login(
            admin_id=request.admin_id,
            password=request.password,
        )
    except AdminAuthConfigurationError as error:
        raise _configuration_error() from error
    except InvalidAdminCredentialsError as error:
        raise AdminAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_ADMIN_CREDENTIALS",
            message="The administrator ID or password is incorrect.",
            include_authenticate_header=True,
        ) from error

    return AdminLoginResponse(
        access_token=token.value,
        expires_in=token.expires_in,
    )
