from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


inventory_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="InventoryBearer",
)


@dataclass(frozen=True, slots=True)
class InventoryPrincipal:
    subject: str = "inventory-reader"


@dataclass(frozen=True, slots=True)
class InventoryAuthHttpError(Exception):
    status_code: int
    code: str
    message: str
    include_authenticate_header: bool = False


def inventory_auth_http_error_handler(
    request: Request,
    error: InventoryAuthHttpError,
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


def require_inventory(
    credentials: HTTPAuthorizationCredentials | None = Security(
        inventory_bearer
    ),
) -> InventoryPrincipal:
    configured_token = os.getenv("INVENTORY_API_TOKEN")
    if configured_token is None or len(configured_token) < 32:
        raise InventoryAuthHttpError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="INVENTORY_AUTH_NOT_CONFIGURED",
            message="Inventory authentication is not configured.",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise InventoryAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVENTORY_TOKEN_REQUIRED",
            message="A valid Inventory Bearer token is required.",
            include_authenticate_header=True,
        )
    if not secrets.compare_digest(credentials.credentials, configured_token):
        raise InventoryAuthHttpError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_INVENTORY_TOKEN",
            message="The Inventory token is invalid.",
            include_authenticate_header=True,
        )
    return InventoryPrincipal()
