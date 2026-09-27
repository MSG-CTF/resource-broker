from __future__ import annotations

from dataclasses import dataclass
import os
import secrets

from fastapi import Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


runtime_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="RuntimeBearer",
)


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    subject: str = "runtime"


@dataclass(frozen=True, slots=True)
class RuntimeAuthHttpError(Exception):
    status_code: int
    code: str
    message: str
    include_authenticate_header: bool = False


def runtime_auth_http_error_handler(
    request: Request,
    error: RuntimeAuthHttpError,
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


def require_runtime(
    credentials: HTTPAuthorizationCredentials | None = Security(
        runtime_bearer
    ),
) -> RuntimePrincipal:
    configured_token = os.getenv("RUNTIME_API_TOKEN")
    if configured_token is None or len(configured_token) < 32:
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="RUNTIME_AUTH_NOT_CONFIGURED",
            message="Runtime authentication is not configured.",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="RUNTIME_TOKEN_REQUIRED",
            message="A valid Runtime Bearer token is required.",
            include_authenticate_header=True,
        )
    if not secrets.compare_digest(
        credentials.credentials,
        configured_token,
    ):
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_RUNTIME_TOKEN",
            message="The Runtime token is invalid.",
            include_authenticate_header=True,
        )
    return RuntimePrincipal()
