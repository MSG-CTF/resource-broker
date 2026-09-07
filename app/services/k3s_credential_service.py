from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import ipaddress
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
import jwt
from sqlalchemy.orm import Session

from app.db.tables import BootstrapJobTable, K3sCredentialTable
from app.domain.enums import BootstrapAction, BootstrapJobStatus
from app.repositories.k3s_credentials import K3sCredentialRepository


_UPLOAD_TOKEN_AUDIENCE = "msg-broker-k3s-credential-upload"
_UPLOAD_TOKEN_ISSUER = "msg-broker"
_UPLOAD_TOKEN_TYPE = "k3s_credential_upload"
_UPLOAD_TOKEN_LIFETIME = timedelta(hours=24)
_UPLOAD_PATH_TEMPLATE = (
    "/v1/agent/bootstrap-jobs/{job_id}/k3s-credentials"
)
_MAX_KUBECONFIG_BYTES = 512 * 1024
_MAX_UPLOAD_AGE = timedelta(minutes=10)
_SERVER_PATTERN = re.compile(
    r"^[ \t]*server:[ \t]*(https://[^\s]+)[ \t]*$",
    re.MULTILINE,
)
_CLIENT_CERTIFICATE_PATTERN = re.compile(
    r"^[ \t]*client-certificate-data:[ \t]*([^\s]+)[ \t]*$",
    re.MULTILINE,
)
_CLIENT_KEY_PATTERN = re.compile(
    r"^[ \t]*client-key-data:[ \t]*([^\s]+)[ \t]*$",
    re.MULTILINE,
)
_CA_PATTERN = re.compile(
    r"^[ \t]*certificate-authority-data:[ \t]*([^\s]+)[ \t]*$",
    re.MULTILINE,
)


class K3sCredentialConfigurationError(RuntimeError):
    pass


class K3sCredentialTargetAddressError(ValueError):
    pass


class InvalidK3sCredentialUploadTokenError(ValueError):
    pass


class K3sCredentialUploadUnavailableError(RuntimeError):
    pass


class K3sCredentialUploadTargetMismatchError(ValueError):
    pass


class InvalidK3sCredentialError(ValueError):
    pass


class K3sCredentialNotFoundError(LookupError):
    pass


class K3sCredentialDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapK3sCredentialUpload:
    upload_url: str
    k3s_server_url: str
    upload_token: str


@dataclass(frozen=True, slots=True)
class DecryptedK3sCredential:
    resource_target_id: UUID
    source_job_id: UUID
    server_url: str
    kubeconfig_base64: str
    client_certificate_fingerprint_sha256: str
    client_certificate_not_after: datetime
    generated_at: datetime
    uploaded_at: datetime


def _required_secret(name: str) -> str:
    value = os.getenv(name, "")
    if value != value.strip() or len(value.encode("utf-8")) < 32:
        raise K3sCredentialConfigurationError(
            f"{name} must contain at least 32 bytes without surrounding "
            "whitespace."
        )
    return value


def _upload_token_secret() -> str:
    return _required_secret("K3S_CREDENTIAL_UPLOAD_TOKEN_SECRET")


def _fernet() -> Fernet:
    secret = _required_secret("K3S_CREDENTIAL_ENCRYPTION_SECRET")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(secret.encode("utf-8")).digest()
    )
    return Fernet(key)


def validate_k3s_credential_storage_configuration() -> None:
    _upload_token_secret()
    _fernet()


def _bootstrap_public_base_url() -> str:
    value = os.getenv(
        "BOOTSTRAP_PUBLIC_BASE_URL",
        "https://agents.mjsec.kr",
    ).strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise K3sCredentialConfigurationError(
            "BOOTSTRAP_PUBLIC_BASE_URL is invalid."
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or '"' in value
        or "\\" in value
    ):
        raise K3sCredentialConfigurationError(
            "BOOTSTRAP_PUBLIC_BASE_URL must be an HTTPS origin."
        )
    return value


def _k3s_server_url(
    *,
    public_ip: str | None,
    private_ip: str | None,
) -> str:
    source = os.getenv("K3S_ADDRESS_SOURCE", "public_ip").strip()
    if source == "public_ip":
        raw_address = public_ip
    elif source == "private_ip":
        raw_address = private_ip
    else:
        raise K3sCredentialConfigurationError(
            "K3S_ADDRESS_SOURCE must be public_ip or private_ip."
        )
    if raw_address is None:
        raise K3sCredentialTargetAddressError
    try:
        address = ipaddress.ip_address(raw_address)
    except ValueError as error:
        raise K3sCredentialTargetAddressError from error
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise K3sCredentialTargetAddressError
    rendered = f"[{address}]" if address.version == 6 else str(address)
    return f"https://{rendered}:6443"


def bootstrap_upload_for_job(
    *,
    job_id: UUID,
    resource_target_id: UUID,
    created_at: datetime,
    public_ip: str | None,
    private_ip: str | None,
) -> BootstrapK3sCredentialUpload:
    if created_at.tzinfo is None:
        raise K3sCredentialConfigurationError(
            "The Bootstrap job creation timestamp must include timezone "
            "information."
        )
    validate_k3s_credential_storage_configuration()
    k3s_server_url = _k3s_server_url(
        public_ip=public_ip,
        private_ip=private_ip,
    )
    token = jwt.encode(
        {
            "iss": _UPLOAD_TOKEN_ISSUER,
            "aud": _UPLOAD_TOKEN_AUDIENCE,
            "type": _UPLOAD_TOKEN_TYPE,
            "sub": str(resource_target_id),
            "bootstrap_job_id": str(job_id),
            "k3s_server_url": k3s_server_url,
            "iat": int(created_at.timestamp()),
            "nbf": int(created_at.timestamp()) - 30,
            "exp": int((created_at + _UPLOAD_TOKEN_LIFETIME).timestamp()),
            "jti": str(job_id),
        },
        _upload_token_secret(),
        algorithm="HS256",
    )
    path = _UPLOAD_PATH_TEMPLATE.format(job_id=job_id)
    return BootstrapK3sCredentialUpload(
        upload_url=f"{_bootstrap_public_base_url()}{path}",
        k3s_server_url=k3s_server_url,
        upload_token=token,
    )


def _validate_k3s_server_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port != 6443
            or parsed.hostname is None
        ):
            raise ValueError
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise InvalidK3sCredentialError from error
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise InvalidK3sCredentialError
    return value


def _decode_upload_token(token: str) -> dict[str, object]:
    try:
        claims: dict[str, object] = jwt.decode(
            token,
            _upload_token_secret(),
            algorithms=["HS256"],
            audience=_UPLOAD_TOKEN_AUDIENCE,
            issuer=_UPLOAD_TOKEN_ISSUER,
            options={
                "require": [
                    "iss",
                    "aud",
                    "type",
                    "sub",
                    "bootstrap_job_id",
                    "k3s_server_url",
                    "iat",
                    "nbf",
                    "exp",
                    "jti",
                ]
            },
        )
    except (jwt.PyJWTError, K3sCredentialConfigurationError) as error:
        if isinstance(error, K3sCredentialConfigurationError):
            raise
        raise InvalidK3sCredentialUploadTokenError from error
    if claims.get("type") != _UPLOAD_TOKEN_TYPE:
        raise InvalidK3sCredentialUploadTokenError
    return claims


def _decode_embedded_value(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidK3sCredentialError from error
    if not decoded:
        raise InvalidK3sCredentialError
    return decoded


def _validate_kubeconfig(
    *,
    kubeconfig_bytes: bytes,
    expected_server_url: str,
    expected_fingerprint: str,
    expected_not_after: datetime,
) -> None:
    try:
        kubeconfig = kubeconfig_bytes.decode("utf-8")
    except UnicodeError as error:
        raise InvalidK3sCredentialError from error
    servers = _SERVER_PATTERN.findall(kubeconfig)
    certificates = _CLIENT_CERTIFICATE_PATTERN.findall(kubeconfig)
    keys = _CLIENT_KEY_PATTERN.findall(kubeconfig)
    authorities = _CA_PATTERN.findall(kubeconfig)
    if not (
        len(servers) == 1
        and len(certificates) == 1
        and len(keys) == 1
        and len(authorities) == 1
        and servers[0] == expected_server_url
    ):
        raise InvalidK3sCredentialError

    try:
        certificate = x509.load_pem_x509_certificate(
            _decode_embedded_value(certificates[0])
        )
        private_key = serialization.load_pem_private_key(
            _decode_embedded_value(keys[0]),
            password=None,
        )
        authority = x509.load_pem_x509_certificate(
            _decode_embedded_value(authorities[0])
        )
        certificate.verify_directly_issued_by(authority)
    except (ValueError, TypeError, InvalidSignature) as error:
        raise InvalidK3sCredentialError from error

    common_names = certificate.subject.get_attributes_for_oid(
        NameOID.COMMON_NAME
    )
    organizations = certificate.subject.get_attributes_for_oid(
        NameOID.ORGANIZATION_NAME
    )
    if (
        not any(attribute.value == "system:admin" for attribute in common_names)
        or not any(
            attribute.value == "system:masters"
            for attribute in organizations
        )
    ):
        raise InvalidK3sCredentialError

    certificate_public_key = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if not hmac.compare_digest(certificate_public_key, private_public_key):
        raise InvalidK3sCredentialError
    actual_fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    if not hmac.compare_digest(actual_fingerprint, expected_fingerprint):
        raise InvalidK3sCredentialError
    if certificate.not_valid_after_utc != expected_not_after:
        raise InvalidK3sCredentialError


class K3sCredentialService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._credentials = K3sCredentialRepository(session)

    def store_from_bootstrap(
        self,
        *,
        path_job_id: UUID,
        token: str,
        bootstrap_job_id: UUID,
        resource_target_id: UUID,
        server_url: str,
        kubeconfig_base64: str,
        client_certificate_fingerprint_sha256: str,
        client_certificate_not_after: datetime,
        generated_at: datetime,
    ) -> K3sCredentialTable:
        claims = _decode_upload_token(token)
        try:
            claim_job_id = UUID(str(claims["bootstrap_job_id"]))
            claim_target_id = UUID(str(claims["sub"]))
            claim_jti = UUID(str(claims["jti"]))
        except (KeyError, ValueError) as error:
            raise InvalidK3sCredentialUploadTokenError from error
        if (
            claim_job_id != path_job_id
            or claim_job_id != bootstrap_job_id
            or claim_jti != claim_job_id
            or claim_target_id != resource_target_id
        ):
            raise K3sCredentialUploadTargetMismatchError
        claim_server_url = claims.get("k3s_server_url")
        if not isinstance(claim_server_url, str) or not hmac.compare_digest(
            claim_server_url,
            server_url,
        ):
            raise K3sCredentialUploadTargetMismatchError
        _validate_k3s_server_url(server_url)

        job = self._session.get(
            BootstrapJobTable,
            path_job_id,
            with_for_update=True,
        )
        now = datetime.now(UTC)
        if (
            job is None
            or job.resource_target_id != resource_target_id
            or job.action
            not in {BootstrapAction.INSTALL, BootstrapAction.UPDATE}
            or job.status
            not in {
                BootstrapJobStatus.APPLYING,
                BootstrapJobStatus.RUNNING,
            }
            or job.deadline_at <= now
        ):
            raise K3sCredentialUploadUnavailableError
        if (
            generated_at > now + timedelta(seconds=60)
            or generated_at < now - _MAX_UPLOAD_AGE
            or client_certificate_not_after <= now
        ):
            raise InvalidK3sCredentialError
        try:
            kubeconfig_bytes = base64.b64decode(
                kubeconfig_base64,
                validate=True,
            )
        except (binascii.Error, ValueError) as error:
            raise InvalidK3sCredentialError from error
        if (
            not kubeconfig_bytes
            or len(kubeconfig_bytes) > _MAX_KUBECONFIG_BYTES
        ):
            raise InvalidK3sCredentialError
        _validate_kubeconfig(
            kubeconfig_bytes=kubeconfig_bytes,
            expected_server_url=server_url,
            expected_fingerprint=client_certificate_fingerprint_sha256,
            expected_not_after=client_certificate_not_after,
        )

        encrypted = _fernet().encrypt(kubeconfig_bytes)
        digest = hashlib.sha256(kubeconfig_bytes).hexdigest()
        credential = self._credentials.get(
            resource_target_id,
            for_update=True,
        )
        if credential is None:
            credential = K3sCredentialTable(
                resource_target_id=resource_target_id,
                source_job_id=path_job_id,
                encrypted_kubeconfig=encrypted,
                kubeconfig_sha256=digest,
                encryption_scheme="fernet-v1",
                server_url=server_url,
                client_certificate_fingerprint_sha256=(
                    client_certificate_fingerprint_sha256
                ),
                client_certificate_not_after=(
                    client_certificate_not_after
                ),
                generated_at=generated_at,
                uploaded_at=now,
                updated_at=now,
            )
            self._credentials.add(credential)
        else:
            credential.source_job_id = path_job_id
            credential.encrypted_kubeconfig = encrypted
            credential.kubeconfig_sha256 = digest
            credential.encryption_scheme = "fernet-v1"
            credential.server_url = server_url
            credential.client_certificate_fingerprint_sha256 = (
                client_certificate_fingerprint_sha256
            )
            credential.client_certificate_not_after = (
                client_certificate_not_after
            )
            credential.generated_at = generated_at
            credential.uploaded_at = now
            credential.updated_at = now
        self._session.commit()
        return credential

    @staticmethod
    def _decrypt(credential: K3sCredentialTable) -> DecryptedK3sCredential:
        if credential.encryption_scheme != "fernet-v1":
            raise K3sCredentialDecryptionError
        try:
            kubeconfig = _fernet().decrypt(
                credential.encrypted_kubeconfig
            )
        except (InvalidToken, K3sCredentialConfigurationError) as error:
            if isinstance(error, K3sCredentialConfigurationError):
                raise
            raise K3sCredentialDecryptionError from error
        digest = hashlib.sha256(kubeconfig).hexdigest()
        if not hmac.compare_digest(digest, credential.kubeconfig_sha256):
            raise K3sCredentialDecryptionError
        return DecryptedK3sCredential(
            resource_target_id=credential.resource_target_id,
            source_job_id=credential.source_job_id,
            server_url=credential.server_url,
            kubeconfig_base64=base64.b64encode(kubeconfig).decode("ascii"),
            client_certificate_fingerprint_sha256=(
                credential.client_certificate_fingerprint_sha256
            ),
            client_certificate_not_after=(
                credential.client_certificate_not_after
            ),
            generated_at=credential.generated_at,
            uploaded_at=credential.uploaded_at,
        )

    def get_for_runtime(
        self,
        resource_target_id: UUID,
    ) -> DecryptedK3sCredential:
        credential = self._credentials.get(
            resource_target_id,
            runtime_visible_only=True,
        )
        if credential is None:
            raise K3sCredentialNotFoundError
        return self._decrypt(credential)

    def list_for_runtime(
        self,
        *,
        after: UUID | None,
        limit: int,
    ) -> tuple[tuple[DecryptedK3sCredential, ...], UUID | None]:
        rows = self._credentials.list_runtime_visible(
            after=after,
            limit=limit,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = page[-1].resource_target_id if has_more and page else None
        return tuple(self._decrypt(row) for row in page), next_cursor
