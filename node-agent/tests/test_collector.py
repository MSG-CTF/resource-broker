import json
from types import SimpleNamespace
import unittest

from msg_broker_node_agent.collector import KubernetesCollector


RESOURCE_TARGET_ID = "1f1c74dd-a671-49d2-9764-87a599e4a9ea"


def ns(**values):
    return SimpleNamespace(**values)


class FakeCoreApi:
    def __init__(self, node, pods, summary):
        self.node = node
        self.pods = pods
        self.summary = summary

    def read_node(self, name):
        self.read_node_name = name
        return self.node

    def list_pod_for_all_namespaces(self, *, field_selector):
        self.field_selector = field_selector
        return ns(items=self.pods)

    def connect_get_node_proxy_with_path(self, name, path, **_kwargs):
        self.proxy_call = (name, path)
        return ns(data=json.dumps(self.summary).encode("utf-8"))


class KubernetesCollectorTest(unittest.TestCase):
    def test_collects_capacity_requests_and_container_usage(self):
        node = ns(
            metadata=ns(
                uid="node-uid-1",
                annotations={
                    "msg-broker.io/resource-target-id": RESOURCE_TARGET_ID
                },
            ),
            status=ns(
                allocatable={
                    "cpu": "2",
                    "memory": "2Gi",
                    "ephemeral-storage": "10Gi",
                },
                conditions=[ns(type="Ready", status="True")],
            ),
        )
        container_status = ns(
            name="nginx",
            container_id="containerd://abc123",
            allocated_resources=None,
            state=ns(running=ns(), waiting=None, terminated=None),
        )
        pod = ns(
            metadata=ns(uid="pod-uid-1", namespace="default", name="nginx"),
            spec=ns(
                containers=[
                    ns(
                        name="nginx",
                        resources=ns(
                            requests={
                                "cpu": "250m",
                                "memory": "128Mi",
                                "ephemeral-storage": "1Gi",
                            }
                        ),
                    )
                ],
                init_containers=None,
                resources=None,
                overhead=None,
            ),
            status=ns(
                phase="Running",
                container_statuses=[container_status],
                init_container_statuses=None,
                ephemeral_container_statuses=None,
            ),
        )
        summary = {
            "pods": [
                {
                    "podRef": {
                        "uid": "pod-uid-1",
                        "namespace": "default",
                        "name": "nginx",
                    },
                    "containers": [
                        {
                            "name": "nginx",
                            "cpu": {"usageNanoCores": 12_500_000},
                            "memory": {"workingSetBytes": 20 * 1024 * 1024},
                            "rootfs": {"usedBytes": 10 * 1024 * 1024},
                            "logs": {"usedBytes": 2 * 1024 * 1024},
                        }
                    ],
                }
            ]
        }
        core_api = FakeCoreApi(node, [pod], summary)
        collector = KubernetesCollector(
            core_api=core_api,
            node_name="canary-node",
            resource_target_annotation="msg-broker.io/resource-target-id",
            resource_target_id_override=None,
        )

        payload = collector.collect()

        self.assertEqual(payload["resource_target_id"], RESOURCE_TARGET_ID)
        self.assertEqual(payload["runtime"]["target_id"], "node-uid-1")
        self.assertTrue(payload["runtime"]["ready"])
        self.assertEqual(
            payload["node_allocatable"],
            {
                "cpu_millicores": 2000,
                "memory_mib": 2048,
                "ephemeral_storage_mib": 10240,
            },
        )
        self.assertEqual(
            payload["allocated_requests"],
            {
                "cpu_millicores": 250,
                "memory_mib": 128,
                "ephemeral_storage_mib": 1024,
            },
        )
        self.assertEqual(
            payload["containers"],
            [
                {
                    "container_id": "containerd://abc123",
                    "container_name": "nginx",
                    "pod_name": "nginx",
                    "namespace": "default",
                    "status": "RUNNING",
                    "cpu_usage_millicores": 12,
                    "memory_usage_mib": 20,
                    "storage_usage_mib": 12,
                }
            ],
        )
        self.assertEqual(core_api.proxy_call, ("canary-node", "stats/summary"))

    def test_ignores_completed_pod_requests(self):
        completed_pod = ns(
            metadata=ns(uid="done", namespace="default", name="done"),
            spec=ns(
                containers=[
                    ns(
                        name="done",
                        resources=ns(requests={"cpu": "1", "memory": "1Gi"}),
                    )
                ],
                init_containers=None,
                resources=None,
                overhead=None,
            ),
            status=ns(
                phase="Succeeded",
                container_statuses=None,
                init_container_statuses=None,
                ephemeral_container_statuses=None,
            ),
        )
        node = ns(
            metadata=ns(uid="node-uid-1", annotations={}),
            status=ns(
                allocatable={
                    "cpu": "1",
                    "memory": "1Gi",
                    "ephemeral-storage": "1Gi",
                },
                conditions=[],
            ),
        )
        collector = KubernetesCollector(
            core_api=FakeCoreApi(node, [completed_pod], {}),
            node_name="canary-node",
            resource_target_annotation="msg-broker.io/resource-target-id",
            resource_target_id_override=RESOURCE_TARGET_ID,
        )

        payload = collector.collect()

        self.assertEqual(
            payload["allocated_requests"],
            {
                "cpu_millicores": 0,
                "memory_mib": 0,
                "ephemeral_storage_mib": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
