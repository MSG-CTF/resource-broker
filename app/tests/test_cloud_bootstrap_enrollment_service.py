from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from uuid import uuid4

from app.domain.enums import BootstrapJobStatus, Provider
from app.services.cloud_bootstrap_enrollment_service import (
    CloudBootstrapEnrollmentService,
    CloudIdentityTargetMismatchError,
)


class Session:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class Jobs:
    def __init__(self, job):
        self.job = job

    def get(self, job_id, *, for_update=False):
        assert for_update
        return self.job if job_id == self.job.job_id else None


class Enrollments:
    def __init__(self, resource):
        self.resource = resource
        self.certificates = []

    def get_resource_target(self, resource_target_id):
        if resource_target_id == self.resource.resource_target_id:
            return self.resource
        return None

    def add_certificate(self, certificate):
        self.certificates.append(certificate)


class Authority:
    def __init__(self):
        self.calls = []

    def issue(self, *, resource_target_id, csr_pem):
        self.calls.append((resource_target_id, csr_pem))
        now = datetime.now(UTC)
        return SimpleNamespace(
            client_certificate_pem="certificate",
            client_ca_pem="ca",
            issuer_fingerprint_sha256="a" * 64,
            serial_number="1234",
            fingerprint_sha256="b" * 64,
            not_before=now,
            not_after=now + timedelta(days=75),
        )


class CloudBootstrapEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.resource_id = uuid4()
        self.job = SimpleNamespace(
            job_id=uuid4(),
            provider=Provider.GCP,
            resource_target_id=self.resource_id,
            status=BootstrapJobStatus.RUNNING,
            enrollment_consumed_at=None,
            enrollment_audience="https://agents.example/enroll/job",
        )
        self.resource = SimpleNamespace(
            resource_target_id=self.resource_id,
            provider_scope_id="project-a",
            provider_instance_id="123456",
            zone="asia-northeast3-a",
            retired_at=None,
        )
        self.session = Session()
        self.authority = Authority()

    def service(self, compute_claims):
        observed = {}

        def verifier(token, audience):
            observed.update(token=token, audience=audience)
            return {"google": {"compute_engine": compute_claims}}

        service = CloudBootstrapEnrollmentService(self.session, verifier)
        service._jobs = Jobs(self.job)
        service._enrollments = Enrollments(self.resource)
        return service, observed

    def test_exact_gcp_vm_claims_issue_once_and_consume_job(self):
        service, observed = self.service(
            {
                "project_id": "project-a",
                "instance_id": "123456",
                "zone": "projects/1/zones/asia-northeast3-a",
            }
        )
        outcome = service.enroll(
            job_id=self.job.job_id,
            token="identity-jwt",
            resource_target_id=self.resource_id,
            csr_pem="csr",
            certificate_authority=self.authority,
        )
        self.assertEqual(outcome.resource_target_id, self.resource_id)
        self.assertEqual(observed["audience"], self.job.enrollment_audience)
        self.assertIsNotNone(self.job.enrollment_consumed_at)
        self.assertEqual(self.session.commits, 1)

    def test_a_different_instance_id_is_rejected(self):
        service, _ = self.service(
            {
                "project_id": "project-a",
                "instance_id": "999999",
                "zone": "asia-northeast3-a",
            }
        )
        with self.assertRaises(CloudIdentityTargetMismatchError):
            service.enroll(
                job_id=self.job.job_id,
                token="identity-jwt",
                resource_target_id=self.resource_id,
                csr_pem="csr",
                certificate_authority=self.authority,
            )
        self.assertEqual(self.authority.calls, [])
        self.assertIsNone(self.job.enrollment_consumed_at)


if __name__ == "__main__":
    unittest.main()
