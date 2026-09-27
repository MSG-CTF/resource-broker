from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID
import requests


class InvalidAzureInstanceIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AzureInstanceIdentity:
    subscription_id: str
    instance_id: str
    nonce: str


_AIA_HOSTS = frozenset(
    {
        "cacerts.digicert.com",
        "cacerts.digicert.cn",
        "cacerts.geotrust.com",
        "caissuers.microsoft.com",
        "www.microsoft.com",
    }
)
_MAX_ATTESTED_DOCUMENT_BYTES = 65536
_MAX_CERTIFICATE_BYTES = 65536
_MAX_CHAIN_DEPTH = 5
_CLOCK_SKEW = timedelta(seconds=60)
_MAX_DOCUMENT_LIFETIME = timedelta(minutes=10)


def azure_attestation_nonce(job_id: UUID) -> str:
    digest = hashlib.sha256(job_id.bytes).digest()
    value = int.from_bytes(digest[:8], "big") % 10_000_000_000
    return f"{value:010d}"


def _required_string(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidAzureInstanceIdentityError
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidAzureInstanceIdentityError
    formats = (
        "%m/%d/%y %H:%M:%S %z",
        "%m/%d/%Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    )
    normalized = value.replace("Z", "+00:00")
    for timestamp_format in formats:
        try:
            return datetime.strptime(normalized, timestamp_format).astimezone(UTC)
        except ValueError:
            continue
    raise InvalidAzureInstanceIdentityError


def _common_name(certificate: x509.Certificate) -> str | None:
    values = certificate.subject.get_attributes_for_oid(
        NameOID.COMMON_NAME
    )
    if not values:
        return None
    value = values[0].value
    return value.lower() if isinstance(value, str) else None


def _signer_certificate(
    certificates: list[x509.Certificate],
) -> x509.Certificate:
    candidates = tuple(
        certificate
        for certificate in certificates
        if (
            (common_name := _common_name(certificate))
            and (
                common_name == "metadata.azure.com"
                or common_name.endswith(".metadata.azure.com")
            )
        )
    )
    if len(candidates) != 1:
        raise InvalidAzureInstanceIdentityError
    return candidates[0]


def _issuer_urls(certificate: x509.Certificate) -> tuple[str, ...]:
    try:
        extension = certificate.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        )
    except x509.ExtensionNotFound:
        return ()
    urls: list[str] = []
    for description in extension.value:
        if description.access_method != AuthorityInformationAccessOID.CA_ISSUERS:
            continue
        location = description.access_location
        if isinstance(location, x509.UniformResourceIdentifier):
            urls.append(location.value)
    return tuple(urls)


def _validate_aia_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidAzureInstanceIdentityError
    if parsed.username is not None or parsed.password is not None:
        raise InvalidAzureInstanceIdentityError
    host = (parsed.hostname or "").lower()
    if host not in _AIA_HOSTS:
        raise InvalidAzureInstanceIdentityError
    if parsed.port is not None and parsed.port not in {80, 443}:
        raise InvalidAzureInstanceIdentityError
    return value


def _load_certificate(value: bytes) -> x509.Certificate:
    try:
        return x509.load_der_x509_certificate(value)
    except ValueError:
        try:
            return x509.load_pem_x509_certificate(value)
        except ValueError as error:
            raise InvalidAzureInstanceIdentityError from error


def _download_issuer_certificate(url: str) -> x509.Certificate:
    current_url = _validate_aia_url(url)
    try:
        for _ in range(3):
            with requests.get(
                current_url,
                timeout=(3.05, 5),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise InvalidAzureInstanceIdentityError
                    current_url = _validate_aia_url(
                        urljoin(current_url, location)
                    )
                    continue
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > (
                    _MAX_CERTIFICATE_BYTES
                ):
                    raise InvalidAzureInstanceIdentityError
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=8192):
                    total += len(chunk)
                    if total > _MAX_CERTIFICATE_BYTES:
                        raise InvalidAzureInstanceIdentityError
                    chunks.append(chunk)
                return _load_certificate(b"".join(chunks))
    except (requests.RequestException, TypeError, ValueError) as error:
        raise InvalidAzureInstanceIdentityError from error
    raise InvalidAzureInstanceIdentityError


def _build_intermediate_chain(
    signer: x509.Certificate,
    embedded: list[x509.Certificate],
) -> tuple[x509.Certificate, ...]:
    by_subject = {
        certificate.subject.public_bytes(): certificate
        for certificate in embedded
        if certificate != signer
    }
    chain: list[x509.Certificate] = []
    current = signer
    seen = {signer.fingerprint(hashes.SHA256())}

    for _ in range(_MAX_CHAIN_DEPTH):
        if current.issuer == current.subject:
            return tuple(chain)
        issuer = by_subject.get(current.issuer.public_bytes())
        if issuer is None:
            urls = _issuer_urls(current)
            if not urls:
                return tuple(chain)
            for url in urls:
                try:
                    issuer = _download_issuer_certificate(url)
                    break
                except InvalidAzureInstanceIdentityError:
                    continue
            if issuer is None:
                raise InvalidAzureInstanceIdentityError
        fingerprint = issuer.fingerprint(hashes.SHA256())
        if fingerprint in seen:
            raise InvalidAzureInstanceIdentityError
        seen.add(fingerprint)
        if issuer.subject != current.issuer:
            raise InvalidAzureInstanceIdentityError
        if issuer.subject == issuer.issuer:
            return tuple(chain)
        chain.append(issuer)
        current = issuer
    raise InvalidAzureInstanceIdentityError


def _verified_pkcs7_content(signature: bytes) -> bytes:
    try:
        certificates = pkcs7.load_der_pkcs7_certificates(signature)
    except ValueError as error:
        raise InvalidAzureInstanceIdentityError from error
    signer = _signer_certificate(certificates)
    intermediates = _build_intermediate_chain(signer, certificates)

    with tempfile.TemporaryDirectory(prefix="msg-azure-attestation-") as directory:
        work_dir = Path(directory)
        signature_path = work_dir / "signature.der"
        content_path = work_dir / "content.json"
        intermediates_path = work_dir / "intermediates.pem"
        signature_path.write_bytes(signature)
        arguments = [
            "openssl",
            "cms",
            "-verify",
            "-binary",
            "-inform",
            "DER",
            "-in",
            str(signature_path),
            "-CAfile",
            requests.certs.where(),
            "-purpose",
            "any",
            "-out",
            str(content_path),
        ]
        if intermediates:
            intermediates_path.write_bytes(
                b"".join(
                    certificate.public_bytes(serialization.Encoding.PEM)
                    for certificate in intermediates
                )
            )
            arguments.extend(["-certfile", str(intermediates_path)])
        try:
            result = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise InvalidAzureInstanceIdentityError from error
        if result.returncode != 0:
            raise InvalidAzureInstanceIdentityError
        content = content_path.read_bytes()
    if not content or len(content) > _MAX_ATTESTED_DOCUMENT_BYTES:
        raise InvalidAzureInstanceIdentityError
    return content


def verify_azure_instance_identity(
    attested_document: str,
    expected_nonce: str,
) -> AzureInstanceIdentity:
    if (
        not isinstance(attested_document, str)
        or not 64 <= len(attested_document) <= _MAX_ATTESTED_DOCUMENT_BYTES
    ):
        raise InvalidAzureInstanceIdentityError
    if (
        not isinstance(expected_nonce, str)
        or len(expected_nonce) != 10
        or not expected_nonce.isdecimal()
    ):
        raise InvalidAzureInstanceIdentityError
    try:
        envelope = json.loads(attested_document)
    except (TypeError, ValueError) as error:
        raise InvalidAzureInstanceIdentityError from error
    if not isinstance(envelope, dict):
        raise InvalidAzureInstanceIdentityError
    if _required_string(envelope, "encoding").lower() != "pkcs7":
        raise InvalidAzureInstanceIdentityError
    try:
        signature = base64.b64decode(
            _required_string(envelope, "signature"),
            validate=True,
        )
    except (binascii.Error, ValueError) as error:
        raise InvalidAzureInstanceIdentityError from error

    try:
        document = json.loads(_verified_pkcs7_content(signature))
    except (TypeError, ValueError) as error:
        raise InvalidAzureInstanceIdentityError from error
    if not isinstance(document, dict):
        raise InvalidAzureInstanceIdentityError
    nonce = _required_string(document, "nonce")
    if nonce != expected_nonce:
        raise InvalidAzureInstanceIdentityError

    timestamp = document.get("timeStamp")
    if not isinstance(timestamp, dict):
        raise InvalidAzureInstanceIdentityError
    created_at = _parse_timestamp(timestamp.get("createdOn"))
    expires_at = _parse_timestamp(timestamp.get("expiresOn"))
    now = datetime.now(UTC)
    if (
        created_at > now + _CLOCK_SKEW
        or expires_at <= now - _CLOCK_SKEW
        or expires_at <= created_at
        or expires_at - created_at > _MAX_DOCUMENT_LIFETIME
    ):
        raise InvalidAzureInstanceIdentityError

    try:
        subscription_id = str(UUID(_required_string(document, "subscriptionId")))
        instance_id = str(UUID(_required_string(document, "vmId")))
    except (TypeError, ValueError) as error:
        raise InvalidAzureInstanceIdentityError from error
    return AzureInstanceIdentity(
        subscription_id=subscription_id,
        instance_id=instance_id,
        nonce=nonce,
    )
