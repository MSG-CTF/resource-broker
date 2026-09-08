from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from typing import Any

from fastapi import Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt


_RUNTIME_TOKEN_ISSUER = "msg-runtime"
_RUNTIME_TOKEN_AUDIENCE = "msg-broker-k3s-credentials"
_RUNTIME_TOKEN_TYPE = "runtime_access"
_MAX_RUNTIME_TOKEN_LIFETIME_SECONDS = 300
_CLOCK_SKEW_SECONDS = 30
runtime_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="RuntimeBearer",
)


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    subject: str
    expires_at: datetime


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


def _settings() -> tuple[str, str]:
    secret = os.getenv("RUNTIME_API_TOKEN_SECRET", "")
    subject = os.getenv("RUNTIME_API_TOKEN_SUBJECT", "runtime")
    if (
        secret != secret.strip()
        or len(secret.encode("utf-8")) < 32
        or not subject
        or subject != subject.strip()
        or len(subject) > 255
    ):
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="RUNTIME_AUTH_NOT_CONFIGURED",
            message="Runtime authentication is not configured.",
        )
    return secret, subject


def require_runtime(
    credentials: HTTPAuthorizationCredentials | None = Security(
        runtime_bearer
    ),
) -> RuntimePrincipal:
    secret, expected_subject = _settings()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="RUNTIME_TOKEN_REQUIRED",
            message="A valid Runtime Bearer token is required.",
            include_authenticate_header=True,
        )
    try:
        claims: dict[str, Any] = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
            audience=_RUNTIME_TOKEN_AUDIENCE,
            issuer=_RUNTIME_TOKEN_ISSUER,
            leeway=_CLOCK_SKEW_SECONDS,
            options={
                "require": [
                    "iss",
                    "aud",
                    "type",
                    "sub",
                    "iat",
                    "nbf",
                    "exp",
                    "jti",
                ]
            },
        )
    except jwt.PyJWTError as error:
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_RUNTIME_TOKEN",
            message="The Runtime token is invalid or expired.",
            include_authenticate_header=True,
        ) from error
    issued_at = claims.get("iat")
    not_before = claims.get("nbf")
    expires_at = claims.get("exp")
    subject = claims.get("sub")
    if (
        claims.get("type") != _RUNTIME_TOKEN_TYPE
        or subject != expected_subject
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(not_before, int)
        or isinstance(not_before, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or not_before > issued_at
        or expires_at <= issued_at
        or expires_at - issued_at > _MAX_RUNTIME_TOKEN_LIFETIME_SECONDS
        or not isinstance(claims.get("jti"), str)
    ):
        raise RuntimeAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_RUNTIME_TOKEN",
            message="The Runtime token is invalid or expired.",
            include_authenticate_header=True,
        )
    return RuntimePrincipal(
        subject=subject,
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )
