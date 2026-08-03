import os
from unittest.mock import patch
import unittest

from msg_broker_node_agent.settings import AgentSettings, AgentSettingsError


class AgentSettingsTest(unittest.TestCase):
    def test_default_collection_interval_is_five_minutes(self):
        with patch.dict(
            os.environ,
            {"NODE_NAME": "node-1", "AGENT_DRY_RUN": "true"},
            clear=True,
        ):
            settings = AgentSettings.from_environment()

        self.assertEqual(settings.interval_seconds, 300)
        self.assertEqual(settings.initial_jitter_seconds, 30)

    def test_plain_http_requires_explicit_override(self):
        with patch.dict(
            os.environ,
            {
                "NODE_NAME": "node-1",
                "BROKER_OBSERVATIONS_URL": (
                    "http://broker/v1/agent/observations"
                ),
            },
            clear=True,
        ):
            with self.assertRaises(AgentSettingsError):
                AgentSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
