from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services.agent_enrollment_service import (
    AgentEnrollmentService,
    InvalidCertificateSigningRequestError,
    IssuedAgentCertificate,
    generate_enrollment_token,
    hash_enrollment_token,
    validate_agent_csr,
)


RESOURCE_TARGET_ID = UUID("11111111-2222-4333-8444-555555555555")


def _csr(resource_target_id: UUID, *, include_san: bool = False) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name(
            [
                x509.NameAttribute(
                    NameOID.COMMON_NAME,
                    str(resource_target_id),
                )
            ]
        )
    )
    if include_san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("not-allowed")]),
            critical=False,
        )
    return (
        builder.sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )


class _FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class _FakeRepository:
    def __init__(self) -> None:
        self.resource = SimpleNamespace(retired_at=None)
        self.tokens = []
        self.certificates = []
        self.revocations = []
        self.enrollment = None

    def get_resource_target(self, resource_target_id):
        return self.resource if resource_target_id == RESOURCE_TARGET_ID else None

    def revoke_unused_tokens(self, **values):
        self.revocations.append(values)

    def add_token(self, token):
        self.tokens.append(token)

    def get_token_for_update(self, token_hash):
        if self.enrollment and self.enrollment.token_hash == token_hash:
            return self.enrollment
        return None

    def add_certificate(self, certificate):
        self.certificates.append(certificate)


class _FakeCertificateAuthority:
    def issue(self, *, resource_target_id, csr_pem):
        self.resource_target_id = resource_target_id
        self.csr_pem = csr_pem
        now = datetime.now(UTC)
        return IssuedAgentCertificate(
            client_certificate_pem="certificate",
            client_ca_pem="ca",
            issuer_fingerprint_sha256="b" * 64,
            serial_number="2000",
            fingerprint_sha256="a" * 64,
            not_before=now - timedelta(minutes=1),
            not_after=now + timedelta(days=75),
        )


class AgentEnrollmentServiceTests(unittest.TestCase):
    def test_token_is_high_entropy_and_hash_does_not_store_plaintext(self):
        token = generate_enrollment_token()

        self.assertTrue(token.startswith("mbe_"))
        self.assertGreaterEqual(len(token), 60)
        self.assertNotIn(token, hash_enrollment_token(token))
        self.assertEqual(len(hash_enrollment_token(token)), 64)

    def test_csr_requires_exact_resource_target_common_name(self):
        parsed = validate_agent_csr(
            resource_target_id=RESOURCE_TARGET_ID,
            csr_pem=_csr(RESOURCE_TARGET_ID),
        )

        self.assertTrue(parsed.is_signature_valid)
        with self.assertRaises(InvalidCertificateSigningRequestError):
            validate_agent_csr(
                resource_target_id=RESOURCE_TARGET_ID,
                csr_pem=_csr(uuid4()),
            )

    def test_csr_rejects_requested_extensions(self):
        with self.assertRaises(InvalidCertificateSigningRequestError):
            validate_agent_csr(
                resource_target_id=RESOURCE_TARGET_ID,
                csr_pem=_csr(RESOURCE_TARGET_ID, include_san=True),
            )

    def test_issue_token_persists_only_hash_and_revokes_previous_token(self):
        session = _FakeSession()
        repository = _FakeRepository()
        service = AgentEnrollmentService(session)  # type: ignore[arg-type]
        service._enrollments = repository

        outcome = service.issue_token(
            resource_target_id=RESOURCE_TARGET_ID,
            expires_in_seconds=600,
        )

        self.assertEqual(session.commit_count, 1)
        self.assertEqual(len(repository.revocations), 1)
        self.assertEqual(len(repository.tokens), 1)
        self.assertNotEqual(repository.tokens[0].token_hash, outcome.token)
        self.assertEqual(
            repository.tokens[0].token_hash,
            hash_enrollment_token(outcome.token),
        )

    def test_enroll_consumes_token_and_records_certificate(self):
        session = _FakeSession()
        repository = _FakeRepository()
        token = generate_enrollment_token()
        repository.enrollment = SimpleNamespace(
            enrollment_id=uuid4(),
            resource_target_id=RESOURCE_TARGET_ID,
            token_hash=hash_enrollment_token(token),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            used_at=None,
            revoked_at=None,
        )
        authority = _FakeCertificateAuthority()
        service = AgentEnrollmentService(session)  # type: ignore[arg-type]
        service._enrollments = repository

        outcome = service.enroll(
            token=token,
            resource_target_id=RESOURCE_TARGET_ID,
            csr_pem=_csr(RESOURCE_TARGET_ID),
            certificate_authority=authority,  # type: ignore[arg-type]
        )

        self.assertEqual(session.commit_count, 1)
        self.assertIsNotNone(repository.enrollment.used_at)
        self.assertEqual(len(repository.certificates), 1)
        self.assertEqual(outcome.resource_target_id, RESOURCE_TARGET_ID)
        self.assertEqual(outcome.serial_number, "2000")


if __name__ == "__main__":
    unittest.main()
