from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.tables import AgentCertificateTable
from app.domain.enums import BootstrapJobStatus, Provider
from app.repositories.agent_enrollments import AgentEnrollmentRepository
from app.repositories.bootstrap_jobs import BootstrapJobRepository
from app.services.agent_enrollment_service import (
    AgentCertificateAuthority,
    AgentCertificateSigningError,
    AgentEnrollmentConfigurationError,
    AgentEnrollmentOutcome,
    InvalidCertificateSigningRequestError,
    ResourceTargetNotFoundError,
    RetiredResourceTargetError,
)
from app.services.aws_instance_identity import (
    AwsInstanceIdentity,
    InvalidAwsInstanceIdentityError,
    verify_aws_instance_identity,
)


class InvalidCloudIdentityError(ValueError):
    pass


class CloudIdentityTargetMismatchError(ValueError):
    pass


class BootstrapEnrollmentUnavailableError(ValueError):
    pass


CloudTokenVerifier = Callable[[str, str], Mapping[str, Any]]
AwsIdentityVerifier = Callable[[str, str], AwsInstanceIdentity]


def verify_gcp_identity_token(token: str, audience: str) -> Mapping[str, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(
            token,
            Request(),
            audience=audience,
        )
    except Exception as error:
        raise InvalidCloudIdentityError from error
    if claims.get("iss") not in {
        "accounts.google.com",
        "https://accounts.google.com",
    }:
        raise InvalidCloudIdentityError
    return claims


def _compute_claims(claims: Mapping[str, Any]) -> Mapping[str, Any]:
    google_claims = claims.get("google")
    if not isinstance(google_claims, Mapping):
        raise InvalidCloudIdentityError
    compute_claims = google_claims.get("compute_engine")
    if not isinstance(compute_claims, Mapping):
        raise InvalidCloudIdentityError
    return compute_claims


class CloudBootstrapEnrollmentService:
    def __init__(
        self,
        session: Session,
        token_verifier: CloudTokenVerifier = verify_gcp_identity_token,
        aws_identity_verifier: AwsIdentityVerifier = verify_aws_instance_identity,
    ) -> None:
        self._session = session
        self._jobs = BootstrapJobRepository(session)
        self._enrollments = AgentEnrollmentRepository(session)
        self._token_verifier = token_verifier
        self._aws_identity_verifier = aws_identity_verifier

    def enroll(
        self,
        *,
        job_id: UUID,
        token: str | None,
        resource_target_id: UUID,
        csr_pem: str,
        aws_instance_identity_document: str | None = None,
        aws_instance_identity_signature: str | None = None,
        certificate_authority: AgentCertificateAuthority | None = None,
    ) -> AgentEnrollmentOutcome:
        job = self._jobs.get(job_id, for_update=True)
        now = datetime.now(UTC)
        if job is None:
            raise BootstrapEnrollmentUnavailableError
        if (
            job.resource_target_id != resource_target_id
            or job.status not in {
                BootstrapJobStatus.APPLYING,
                BootstrapJobStatus.RUNNING,
            }
            or job.enrollment_consumed_at is not None
            or job.deadline_at <= now
        ):
            raise BootstrapEnrollmentUnavailableError

        resource = self._enrollments.get_resource_target(resource_target_id)
        if resource is None:
            raise ResourceTargetNotFoundError
        if resource.retired_at is not None:
            raise RetiredResourceTargetError

        if job.provider is Provider.GCP:
            if token is None:
                raise InvalidCloudIdentityError
            try:
                claims = self._token_verifier(token, job.enrollment_audience)
            except InvalidCloudIdentityError:
                raise
            except Exception as error:
                raise InvalidCloudIdentityError from error
            compute = _compute_claims(claims)
            claim_zone = str(compute.get("zone", "")).rsplit(
                "/", maxsplit=1
            )[-1]
            if (
                str(compute.get("project_id", ""))
                != resource.provider_scope_id
                or str(compute.get("instance_id", ""))
                != resource.provider_instance_id
                or claim_zone != resource.zone
            ):
                raise CloudIdentityTargetMismatchError
        elif job.provider is Provider.AWS:
            if (
                aws_instance_identity_document is None
                or aws_instance_identity_signature is None
            ):
                raise InvalidCloudIdentityError
            try:
                identity = self._aws_identity_verifier(
                    aws_instance_identity_document,
                    aws_instance_identity_signature,
                )
            except InvalidAwsInstanceIdentityError as error:
                raise InvalidCloudIdentityError from error
            except Exception as error:
                raise InvalidCloudIdentityError from error
            if (
                identity.account_id != resource.account.external_account_id
                or identity.region != resource.provider_scope_id
                or identity.instance_id != resource.provider_instance_id
            ):
                raise CloudIdentityTargetMismatchError
        else:
            raise BootstrapEnrollmentUnavailableError

        authority = certificate_authority or AgentCertificateAuthority.from_environment()
        issued = authority.issue(
            resource_target_id=resource_target_id,
            csr_pem=csr_pem,
        )
        job.enrollment_consumed_at = now
        self._enrollments.add_certificate(
            AgentCertificateTable(
                certificate_id=uuid4(),
                resource_target_id=resource_target_id,
                enrollment_id=None,
                issuer_fingerprint_sha256=issued.issuer_fingerprint_sha256,
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
