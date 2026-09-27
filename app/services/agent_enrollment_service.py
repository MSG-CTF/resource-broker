from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy.orm import Session

from app.db.tables import AgentCertificateTable, AgentEnrollmentTokenTable
from app.repositories.agent_enrollments import AgentEnrollmentRepository


_TOKEN_PREFIX = "mbe_"
_DEFAULT_CERTIFICATE_DAYS = 75


class ResourceTargetNotFoundError(LookupError):
    pass


class RetiredResourceTargetError(ValueError):
    pass


class InvalidEnrollmentTokenError(ValueError):
    pass


class EnrollmentResourceTargetMismatchError(ValueError):
    pass


class InvalidCertificateSigningRequestError(ValueError):
    pass


class AgentEnrollmentConfigurationError(RuntimeError):
    pass


class AgentCertificateSigningError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EnrollmentTokenOutcome:
    enrollment_id: UUID
    resource_target_id: UUID
    token: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedAgentCertificate:
    client_certificate_pem: str
    client_ca_pem: str
    issuer_fingerprint_sha256: str
    serial_number: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime


@dataclass(frozen=True, slots=True)
class AgentEnrollmentOutcome:
    resource_target_id: UUID
    client_certificate_pem: str
    client_ca_pem: str
    serial_number: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    issued_at: datetime


def generate_enrollment_token() -> str:
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(48)}"


def hash_enrollment_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _certificate_days() -> int:
    raw_value = os.getenv(
        "AGENT_ENROLLMENT_CERTIFICATE_DAYS",
        str(_DEFAULT_CERTIFICATE_DAYS),
    ).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise AgentEnrollmentConfigurationError(
            "AGENT_ENROLLMENT_CERTIFICATE_DAYS must be an integer."
        ) from error
    if value < 1 or value > 90:
        raise AgentEnrollmentConfigurationError(
            "AGENT_ENROLLMENT_CERTIFICATE_DAYS must be between 1 and 90."
        )
    return value


def validate_agent_csr(
    *,
    resource_target_id: UUID,
    csr_pem: str,
) -> x509.CertificateSigningRequest:
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise InvalidCertificateSigningRequestError from error

    if not csr.is_signature_valid:
        raise InvalidCertificateSigningRequestError

    common_names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if (
        len(csr.subject) != 1
        or len(common_names) != 1
        or common_names[0].value.lower() != str(resource_target_id)
    ):
        raise InvalidCertificateSigningRequestError

    if len(csr.extensions) != 0:
        raise InvalidCertificateSigningRequestError

    public_key = csr.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve,
        ec.SECP256R1,
    ):
        raise InvalidCertificateSigningRequestError

    return csr


class AgentCertificateAuthority:
    def __init__(
        self,
        *,
        ca_directory: Path,
        openssl_binary: str,
        certificate_days: int,
    ) -> None:
        self._ca_directory = ca_directory
        self._openssl_binary = openssl_binary
        self._certificate_days = certificate_days
        self._config_file = ca_directory / "openssl.cnf"
        self._ca_certificate_file = ca_directory / "ca.crt"
        self._client_ca_file = ca_directory / "ca-chain.crt"
        self._lock_file = ca_directory / ".signing.lock"

        required_files = (
            self._config_file,
            self._ca_certificate_file,
            self._client_ca_file,
            ca_directory / "private" / "ca.key",
            ca_directory / "index.txt",
            ca_directory / "serial",
        )
        if not ca_directory.is_absolute() or any(
            not path.is_file() for path in required_files
        ):
            raise AgentEnrollmentConfigurationError(
                "The Agent enrollment CA directory is incomplete."
            )

        try:
            self._ca_certificate = x509.load_pem_x509_certificate(
                self._ca_certificate_file.read_bytes()
            )
            self._client_ca_pem = self._client_ca_file.read_text(
                encoding="ascii"
            )
        except (OSError, ValueError, UnicodeError) as error:
            raise AgentEnrollmentConfigurationError(
                "The Agent enrollment CA files could not be loaded."
            ) from error

        minimum_valid_until = datetime.now(UTC) + timedelta(
            days=certificate_days
        )
        if self._ca_certificate.not_valid_after_utc <= minimum_valid_until:
            raise AgentEnrollmentConfigurationError(
                "The Agent enrollment CA must be rotated before issuing certificates."
            )

    @classmethod
    def from_environment(cls) -> "AgentCertificateAuthority":
        raw_directory = os.getenv("AGENT_ENROLLMENT_CA_DIR", "").strip()
        if not raw_directory:
            raise AgentEnrollmentConfigurationError(
                "AGENT_ENROLLMENT_CA_DIR must be configured."
            )
        openssl_binary = shutil.which("openssl")
        if openssl_binary is None:
            raise AgentEnrollmentConfigurationError(
                "The OpenSSL executable is not installed."
            )
        return cls(
            ca_directory=Path(raw_directory),
            openssl_binary=openssl_binary,
            certificate_days=_certificate_days(),
        )

    def issue(
        self,
        *,
        resource_target_id: UUID,
        csr_pem: str,
    ) -> IssuedAgentCertificate:
        validate_agent_csr(
            resource_target_id=resource_target_id,
            csr_pem=csr_pem,
        )

        try:
            with tempfile.TemporaryDirectory(
                prefix="msg-broker-enrollment-"
            ) as temporary_directory:
                temporary_path = Path(temporary_directory)
                csr_file = temporary_path / "agent.csr"
                certificate_file = temporary_path / "agent.crt"
                csr_file.write_text(csr_pem, encoding="ascii")

                try:
                    import fcntl
                except ModuleNotFoundError as error:
                    raise AgentEnrollmentConfigurationError(
                        "Agent certificate signing requires a Linux host."
                    ) from error

                with self._lock_file.open("a+b") as lock_handle:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                    result = subprocess.run(
                        [
                            self._openssl_binary,
                            "ca",
                            "-batch",
                            "-notext",
                            "-config",
                            str(self._config_file),
                            "-extensions",
                            "client_cert",
                            "-days",
                            str(self._certificate_days),
                            "-in",
                            str(csr_file),
                            "-out",
                            str(certificate_file),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

                if result.returncode != 0 or not certificate_file.is_file():
                    raise AgentCertificateSigningError

                certificate_pem = certificate_file.read_text(
                    encoding="ascii"
                )
                certificate = x509.load_pem_x509_certificate(
                    certificate_pem.encode("ascii")
                )
        except InvalidCertificateSigningRequestError:
            raise
        except AgentCertificateSigningError:
            raise
        except (
            OSError,
            subprocess.SubprocessError,
            UnicodeError,
            ValueError,
        ) as error:
            raise AgentCertificateSigningError from error

        common_names = certificate.subject.get_attributes_for_oid(
            NameOID.COMMON_NAME
        )
        if (
            len(common_names) != 1
            or common_names[0].value.lower() != str(resource_target_id)
            or certificate.issuer != self._ca_certificate.subject
        ):
            raise AgentCertificateSigningError

        return IssuedAgentCertificate(
            client_certificate_pem=certificate_pem,
            client_ca_pem=self._client_ca_pem,
            issuer_fingerprint_sha256=self._ca_certificate.fingerprint(
                hashes.SHA256()
            ).hex(),
            serial_number=format(certificate.serial_number, "x"),
            fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
            not_before=certificate.not_valid_before_utc,
            not_after=certificate.not_valid_after_utc,
        )


class AgentEnrollmentService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._enrollments = AgentEnrollmentRepository(session)

    def issue_token(
        self,
        *,
        resource_target_id: UUID,
        expires_in_seconds: int,
    ) -> EnrollmentTokenOutcome:
        now = datetime.now(UTC)
        resource = self._enrollments.get_resource_target(resource_target_id)
        if resource is None:
            raise ResourceTargetNotFoundError
        if resource.retired_at is not None:
            raise RetiredResourceTargetError

        self._enrollments.revoke_unused_tokens(
            resource_target_id=resource_target_id,
            revoked_at=now,
        )
        raw_token = generate_enrollment_token()
        enrollment_id = uuid4()
        expires_at = now + timedelta(seconds=expires_in_seconds)
        self._enrollments.add_token(
            AgentEnrollmentTokenTable(
                enrollment_id=enrollment_id,
                resource_target_id=resource_target_id,
                token_hash=hash_enrollment_token(raw_token),
                expires_at=expires_at,
                created_at=now,
            )
        )
        self._session.commit()
        return EnrollmentTokenOutcome(
            enrollment_id=enrollment_id,
            resource_target_id=resource_target_id,
            token=raw_token,
            expires_at=expires_at,
            created_at=now,
        )

    def enroll(
        self,
        *,
        token: str,
        resource_target_id: UUID,
        csr_pem: str,
        certificate_authority: AgentCertificateAuthority | None = None,
    ) -> AgentEnrollmentOutcome:
        now = datetime.now(UTC)
        enrollment = self._enrollments.get_token_for_update(
            hash_enrollment_token(token)
        )
        if (
            enrollment is None
            or enrollment.used_at is not None
            or enrollment.revoked_at is not None
            or enrollment.expires_at <= now
        ):
            raise InvalidEnrollmentTokenError
        if enrollment.resource_target_id != resource_target_id:
            raise EnrollmentResourceTargetMismatchError

        resource = self._enrollments.get_resource_target(resource_target_id)
        if resource is None:
            raise ResourceTargetNotFoundError
        if resource.retired_at is not None:
            raise RetiredResourceTargetError

        authority = certificate_authority or (
            AgentCertificateAuthority.from_environment()
        )
        issued = authority.issue(
            resource_target_id=resource_target_id,
            csr_pem=csr_pem,
        )
        enrollment.used_at = now
        self._enrollments.add_certificate(
            AgentCertificateTable(
                certificate_id=uuid4(),
                resource_target_id=resource_target_id,
                enrollment_id=enrollment.enrollment_id,
                issuer_fingerprint_sha256=(
                    issued.issuer_fingerprint_sha256
                ),
                serial_number=issued.serial_number,
                fingerprint_sha256=issued.fingerprint_sha256,
                not_before=issued.not_before,
                not_after=issued.not_after,
                issued_at=now,
            )
        )
        self._session.commit()

        return AgentEnrollmentOutcome(
            resource_target_id=resource_target_id,
            client_certificate_pem=issued.client_certificate_pem,
            client_ca_pem=issued.client_ca_pem,
            serial_number=issued.serial_number,
            fingerprint_sha256=issued.fingerprint_sha256,
            not_before=issued.not_before,
            not_after=issued.not_after,
            issued_at=now,
        )
