"""Keep GCP inventory compatible with the installed Compute SDK request type."""
import unittest

from google.cloud import compute_v1

from app.adapters.gcp import GcpAdapter


class EmptyInstancesClient:
    def aggregated_list(self, *, request):
        assert request.project == "msg-broker-test-project"
        assert request.max_results == 50
        return ()


class GcpRequestCompatTest(unittest.TestCase):
    def test_installed_sdk_accepts_inventory_request(self):
        adapter = GcpAdapter(
            project_id="msg-broker-test-project",
            instances_client=EmptyInstancesClient(),
            request_factory=compute_v1.AggregatedListInstancesRequest,
        )

        self.assertEqual(adapter.list_instances(), ())


if __name__ == "__main__":
    unittest.main()
