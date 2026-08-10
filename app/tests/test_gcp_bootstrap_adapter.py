from types import SimpleNamespace
import unittest

from app.adapters.gcp_bootstrap import (
    GcpBootstrapAdapter,
    GcpBootstrapCleanupError,
    GcpBootstrapConflictError,
    GcpBootstrapTarget,
)


class Operation:
    def result(self, timeout: int) -> None:
        self.timeout = timeout


class ComputeTypes:
    class InstancesSetLabelsRequest:
        def __init__(self, *, labels, label_fingerprint):
            self.labels = labels
            self.label_fingerprint = label_fingerprint


class Instances:
    def __init__(self, labels):
        self.labels = dict(labels)
        self.fingerprint = "fp-1"
        self.updates = []

    def get(self, **_):
        return SimpleNamespace(
            labels=dict(self.labels),
            label_fingerprint=self.fingerprint,
        )

    def set_labels(self, **kwargs):
        body = kwargs["instances_set_labels_request_resource"]
        self.updates.append(dict(body.labels))
        self.labels = dict(body.labels)
        self.fingerprint = f"fp-{len(self.updates) + 1}"
        return Operation()


class OsConfigTypes:
    @staticmethod
    def OSPolicyAssignment(**kwargs):
        return kwargs


class OsConfig:
    def __init__(self):
        self.created = None

    def create_os_policy_assignment(self, **kwargs):
        self.created = kwargs
        return Operation()


def adapter(instances, os_config=None):
    return GcpBootstrapAdapter(
        GcpBootstrapTarget("project-a", "asia-northeast3-a", "vm-a", "123"),
        instances,
        os_config or OsConfig(),
        ComputeTypes,
        OsConfigTypes,
    )


class GcpBootstrapAdapterTests(unittest.TestCase):
    def test_temporary_label_preserves_and_then_restores_other_labels(self):
        instances = Instances({"environment": "prod", "owner": "platform"})
        subject = adapter(instances)

        subject.ensure_temporary_label("msg-broker-bootstrap-job", "job-abc")
        self.assertEqual(
            instances.labels,
            {
                "environment": "prod",
                "owner": "platform",
                "msg-broker-bootstrap-job": "job-abc",
            },
        )

        subject.remove_temporary_label("msg-broker-bootstrap-job", "job-abc")
        self.assertEqual(
            instances.labels,
            {"environment": "prod", "owner": "platform"},
        )

    def test_reserved_label_is_never_overwritten(self):
        instances = Instances({"msg-broker-bootstrap-job": "somebody-else"})
        with self.assertRaises(GcpBootstrapConflictError):
            adapter(instances).ensure_temporary_label(
                "msg-broker-bootstrap-job",
                "job-abc",
            )
        self.assertEqual(instances.updates, [])

    def test_cleanup_does_not_delete_a_label_changed_by_another_actor(self):
        instances = Instances({"msg-broker-bootstrap-job": "new-owner"})
        with self.assertRaises(GcpBootstrapCleanupError):
            adapter(instances).remove_temporary_label(
                "msg-broker-bootstrap-job",
                "job-abc",
            )
        self.assertEqual(instances.updates, [])

    def test_assignment_targets_only_the_unique_temporary_label(self):
        instances = Instances({})
        os_config = OsConfig()
        subject = adapter(instances, os_config)
        subject.ensure_assignment(
            assignment_id="msg-bootstrap-abc",
            label_key="msg-broker-bootstrap-job",
            label_value="job-abc",
            runner_url="https://agents.example/runner.sh",
            runner_sha256="0" * 64,
            job_id="job-id",
        )
        assignment = os_config.created["os_policy_assignment"]
        self.assertEqual(
            assignment["instance_filter"]["inclusion_labels"],
            [{"labels": {"msg-broker-bootstrap-job": "job-abc"}}],
        )
        remote = assignment["os_policies"][0]["resource_groups"][0]["resources"][0]["exec"]["enforce"]["file"]["remote"]
        self.assertEqual(remote["sha256_checksum"], "0" * 64)


if __name__ == "__main__":
    unittest.main()
