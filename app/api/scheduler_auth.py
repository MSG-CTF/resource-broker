from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


scheduler_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="SchedulerBearer",
)


@dataclass(frozen=True, slots=True)
class SchedulerPrincipal:
    subject: str = "scheduler"


@dataclass(frozen=True, slots=True)
class SchedulerAuthHttpError(Exception):
    status_code: int
    code: str
    message: str
    include_authenticate_header: bool = False


def scheduler_auth_http_error_handler(
    request: Request,
    error: SchedulerAuthHttpError,
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


def require_scheduler(
    credentials: HTTPAuthorizationCredentials | None = Security(
        scheduler_bearer
    ),
) -> SchedulerPrincipal:
    configured_token = os.getenv("SCHEDULER_API_TOKEN")
    if configured_token is None or len(configured_token) < 32:
        raise SchedulerAuthHttpError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SCHEDULER_AUTH_NOT_CONFIGURED",
            message="Scheduler authentication is not configured.",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise SchedulerAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="SCHEDULER_TOKEN_REQUIRED",
            message="A valid Scheduler Bearer token is required.",
            include_authenticate_header=True,
        )
    if not secrets.compare_digest(credentials.credentials, configured_token):
        raise SchedulerAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_SCHEDULER_TOKEN",
            message="The Scheduler token is invalid.",
            include_authenticate_header=True,
        )
    return SchedulerPrincipal()
