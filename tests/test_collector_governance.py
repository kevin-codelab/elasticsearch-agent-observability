import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bootstrap_observability  # noqa: E402
import render_collector_config  # noqa: E402


DISCOVERY_SAMPLE = {
    "files_scanned": 12,
    "detected_modules": [{"module_kind": "tool_registry"}],
    "recommended_ingest_modes": [{"mode": "collector", "score": 0.94}],
}


class CollectorGovernanceTests(unittest.TestCase):
    def test_bootstrap_parses_governance_flags(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "bootstrap_observability.py",
                "--workspace",
                "/tmp/ws",
                "--output-dir",
                "/tmp/out",
                "--es-url",
                "http://localhost:9200",
                "--sampling-ratio",
                "0.2",
                "--send-queue-size",
                "4096",
                "--retry-initial-interval",
                "1s",
                "--retry-max-interval",
                "30s",
            ],
        ):
            args = bootstrap_observability.parse_args()
        self.assertEqual(args.sampling_ratio, 0.2)
        self.assertEqual(args.send_queue_size, 4096)
        self.assertEqual(args.retry_initial_interval, "1s")
        self.assertEqual(args.retry_max_interval, "30s")

    def test_render_config_includes_queue_and_retry_blocks(self) -> None:
        rendered = render_collector_config.render_config(
            DISCOVERY_SAMPLE,
            es_url="http://localhost:9200",
            index_prefix="agent-obsv",
            environment="dev",
            service_name="agent-runtime",
            sampling_ratio=0.2,
            send_queue_size=4096,
            retry_initial_interval="1s",
            retry_max_interval="30s",
        )
        self.assertIn("probabilistic_sampler", rendered)
        self.assertIn("sampling_percentage: 20.0", rendered)
        self.assertIn("sending_queue:", rendered)
        self.assertIn("queue_size: 4096", rendered)
        self.assertIn("retry_on_failure:", rendered)
        self.assertIn("initial_interval: 1s", rendered)
        self.assertIn("max_interval: 30s", rendered)


if __name__ == "__main__":
    unittest.main()
