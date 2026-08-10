import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.domain.enums import BootstrapAction
from app.services.bootstrap_artifacts import (
    artifact_for_version,
    render_runner_script,
    runner_sha256,
)


class BootstrapArtifactTests(unittest.TestCase):
    def test_artifact_is_checksum_pinned_and_runner_contains_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "msg-broker-node-agent-bootstrap-0.1.0.tar.gz"
            path.write_bytes(b"immutable bundle")
            with patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_ARTIFACT_DIR": directory,
                    "BOOTSTRAP_PUBLIC_BASE_URL": "https://agents.example",
                },
            ):
                artifact = artifact_for_version("0.1.0")

            self.assertEqual(
                artifact.sha256,
                hashlib.sha256(b"immutable bundle").hexdigest(),
            )
            script = render_runner_script(
                job_id=uuid4(),
                resource_target_id=uuid4(),
                action=BootstrapAction.INSTALL,
                bootstrap_version="0.1.0",
                artifact_url=artifact.public_url,
                artifact_sha256=artifact.sha256,
                enrollment_url="https://agents.example/v1/agent/bootstrap-enrollments/job",
                enrollment_audience_value="https://agents.example/v1/agent/bootstrap-enrollments/job",
                k3s_version="v1.33.3+k3s1",
                agent_image=f"registry.example/agent@sha256:{'a' * 64}",
            )
            self.assertIn("sha256sum --check --strict", script)
            self.assertIn("MSG_BROKER_ENROLLMENT_MODE=gcp-identity", script)
            self.assertNotIn("mbe_", script)
            self.assertEqual(len(runner_sha256(script)), 64)


if __name__ == "__main__":
    unittest.main()
