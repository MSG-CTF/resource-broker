from __future__ import annotations

import hmac
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt


ADMIN_TOKEN_ISSUER = "msg-broker"
ADMIN_TOKEN_AUDIENCE = "msg-broker-admin"
ADMIN_TOKEN_TYPE = "admin_access"
DEFAULT_ADMIN_TOKEN_TTL_SECONDS = 28_800
MINIMUM_TOKEN_SECRET_BYTES = 32


class AdminAuthConfigurationError(RuntimeError):
    pass


class InvalidAdminCredentialsError(ValueError):
    pass


class InvalidAdminTokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AdminAuthSettings:
    admin_id: str
    admin_password: str
    token_secret: str
    token_ttl_seconds: int

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> AdminAuthSettings:
        values = os.environ if environment is None else environment
        admin_id = values.get("ADMIN_ID", "")
        admin_password = values.get("ADMIN_PASSWORD", "")
        token_secret = values.get("ADMIN_TOKEN_SECRET", "")
        raw_ttl = values.get(
            "ADMIN_TOKEN_TTL_SECONDS",
            str(DEFAULT_ADMIN_TOKEN_TTL_SECONDS),
        )

        if not admin_id or admin_id.strip() != admin_id:
            raise AdminAuthConfigurationError(
                "ADMIN_ID must be configured without surrounding whitespace."
            )
        if not admin_password:
            raise AdminAuthConfigurationError(
                "ADMIN_PASSWORD must be configured."
            )
        if len(token_secret.encode("utf-8")) < MINIMUM_TOKEN_SECRET_BYTES:
            raise AdminAuthConfigurationError(
                "ADMIN_TOKEN_SECRET must contain at least 32 bytes."
            )
        try:
            token_ttl_seconds = int(raw_ttl)
        except ValueError as error:
            raise AdminAuthConfigurationError(
                "ADMIN_TOKEN_TTL_SECONDS must be an integer."
            ) from error
        if not 300 <= token_ttl_seconds <= 86_400:
            raise AdminAuthConfigurationError(
                "ADMIN_TOKEN_TTL_SECONDS must be between 300 and 86400."
            )

        return cls(
            admin_id=admin_id,
            admin_password=admin_password,
            token_secret=token_secret,
            token_ttl_seconds=token_ttl_seconds,
        )


@dataclass(frozen=True, slots=True)
class AdminAccessToken:
    value: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    admin_id: str
    expires_at: datetime


AdminAuthSettingsProvider = Callable[[], AdminAuthSettings]


class AdminAuthService:
    def __init__(
        self,
        settings_provider: AdminAuthSettingsProvider = (
            AdminAuthSettings.from_environment
        ),
    ) -> None:
        self._settings_provider = settings_provider

    def login(self, admin_id: str, password: str) -> AdminAccessToken:
        settings = self._settings_provider()
        id_matches = hmac.compare_digest(
            admin_id.encode("utf-8"),
            settings.admin_id.encode("utf-8"),
        )
        password_matches = hmac.compare_digest(
            password.encode("utf-8"),
            settings.admin_password.encode("utf-8"),
        )
        if not (id_matches and password_matches):
            raise InvalidAdminCredentialsError

        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(
            seconds=settings.token_ttl_seconds
        )
        payload = {
            "sub": settings.admin_id,
            "iss": ADMIN_TOKEN_ISSUER,
            "aud": ADMIN_TOKEN_AUDIENCE,
            "type": ADMIN_TOKEN_TYPE,
            "iat": issued_at,
            "exp": expires_at,
            "jti": str(uuid4()),
        }
        encoded_token = jwt.encode(
            payload,
            settings.token_secret,
            algorithm="HS256",
        )
        return AdminAccessToken(
            value=encoded_token,
            expires_in=settings.token_ttl_seconds,
        )

    def authenticate(self, token: str) -> AdminPrincipal:
        settings = self._settings_provider()
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                settings.token_secret,
                algorithms=["HS256"],
                audience=ADMIN_TOKEN_AUDIENCE,
                issuer=ADMIN_TOKEN_ISSUER,
                options={
                    "require": [
                        "sub",
                        "iss",
                        "aud",
                        "type",
                        "iat",
                        "exp",
                        "jti",
                    ],
                },
            )
        except jwt.PyJWTError as error:
            raise InvalidAdminTokenError from error

        subject = payload.get("sub")
        token_type = payload.get("type")
        expires_at = payload.get("exp")
        if (
            not isinstance(subject, str)
            or not hmac.compare_digest(
                subject.encode("utf-8"),
                settings.admin_id.encode("utf-8"),
            )
            or token_type != ADMIN_TOKEN_TYPE
            or not isinstance(expires_at, int)
        ):
            raise InvalidAdminTokenError

        return AdminPrincipal(
            admin_id=subject,
            expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
        )
