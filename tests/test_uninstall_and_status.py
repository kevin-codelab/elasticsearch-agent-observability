"""Uninstall and status script tests (ES mocked)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import status  # noqa: E402
import uninstall  # noqa: E402
from common import ESConfig, SkillError  # noqa: E402


def _cfg() -> ESConfig:
    return ESConfig(es_url="http://localhost:9200")


class UninstallTests(unittest.TestCase):
    def test_dry_run_builds_plan_without_es_calls(self) -> None:
        with mock.patch.object(uninstall, "es_request", side_effect=AssertionError("no es calls in dry-run")):
            summary = uninstall.run_uninstall(
                _cfg(),
                index_prefix="agent-obsv",
                confirm=False,
                keep_data_stream=False,
                kibana_url="",
                kibana_space="default",
                kibana_assets_file="",
            )
        self.assertTrue(summary["dry_run"])
        assets = [step["asset"] for step in summary["plan"]]
        # Ordering invariant: data_stream first (if present), ilm_policy last.
        self.assertEqual(assets[0], "data_stream")
        self.assertEqual(assets[-1], "ilm_policy")
        self.assertIn("index_template", assets)
        self.assertIn("ingest_pipeline", assets)

    def test_keep_data_stream_drops_that_step(self) -> None:
        with mock.patch.object(uninstall, "es_request", side_effect=AssertionError("no es calls in dry-run")):
            summary = uninstall.run_uninstall(
                _cfg(),
                index_prefix="agent-obsv",
                confirm=False,
                keep_data_stream=True,
                kibana_url="",
                kibana_space="default",
                kibana_assets_file="",
            )
        assets = [step["asset"] for step in summary["plan"]]
        self.assertNotIn("data_stream", assets)

    def test_confirm_treats_404_as_already_absent(self) -> None:
        def fake_es(config, method, path, payload=None):
            if method == "DELETE":
                # Mixed: one success, one 404.
                if "ilm" in path:
                    raise SkillError("Elasticsearch HTTP 404: not_found")
                return {"acknowledged": True}
            return {"acknowledged": True}

        with mock.patch.object(uninstall, "es_request", side_effect=fake_es):
            summary = uninstall.run_uninstall(
                _cfg(),
                index_prefix="agent-obsv",
                confirm=True,
                keep_data_stream=False,
                kibana_url="",
                kibana_space="default",
                kibana_assets_file="",
            )
        ilm = next(item for item in summary["results"] if item["asset"] == "ilm_policy")
        self.assertEqual(ilm["status"], "already_absent")
        non_ilm = [item for item in summary["results"] if item["asset"] != "ilm_policy"]
        self.assertTrue(all(item["status"] == "deleted" for item in non_ilm))

    def test_confirm_surfaces_real_failure(self) -> None:
        def fake_es(config, method, path, payload=None):
            if method == "DELETE" and "index_template" in path:
                raise SkillError("Elasticsearch HTTP 500: boom")
            return {"acknowledged": True}

        with mock.patch.object(uninstall, "es_request", side_effect=fake_es):
            summary = uninstall.run_uninstall(
                _cfg(),
                index_prefix="agent-obsv",
                confirm=True,
                keep_data_stream=False,
                kibana_url="",
                kibana_space="default",
                kibana_assets_file="",
            )
        item = next(i for i in summary["results"] if i["asset"] == "index_template")
        self.assertEqual(item["status"], "failed")
        self.assertIn("500", item["detail"])


class StatusTests(unittest.TestCase):
    def _make_es(self, *, present: set[str], ds_present: bool = True, ds_count: int = 42):
        """Build a fake es_request that answers by path."""
        def fake_es(config, method, path, payload=None):
            if path == "/":
                return {"version": {"number": "9.0.0"}}
            if path.endswith("/_count"):
                return {"count": ds_count}
            if path.startswith("/_data_stream/"):
                if not ds_present:
                    raise SkillError("Elasticsearch HTTP 404: index_not_found_exception")
                return {
                    "data_streams": [
                        {
                            "name": path.rsplit("/", 1)[1],
                            "generation": 3,
                            "template": "agent-obsv-events-template",
                            "indices": [{"index_name": ".ds-agent-obsv-events-000001"}],
                        }
                    ]
                }
            # Asset probes: present => 200, absent => 404.
            for label in present:
                if label in path:
                    return {"ok": True}
            raise SkillError("Elasticsearch HTTP 404: not_found")
        return fake_es

    def test_all_present_is_ready(self) -> None:
        fake = self._make_es(
            present={"ilm/policy", "ingest/pipeline", "component_template", "index_template"},
            ds_present=True,
        )
        with mock.patch.object(status, "es_request", side_effect=fake):
            result = status.run_status(_cfg(), index_prefix="agent-obsv")
        self.assertEqual(result["overall"], "ready")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["data_stream"]["status"], "present")
        self.assertEqual(result["data_stream"]["doc_count"], 42)

    def test_missing_template_is_degraded(self) -> None:
        fake = self._make_es(
            present={"ilm/policy", "ingest/pipeline", "component_template"},  # no index_template
            ds_present=True,
        )
        with mock.patch.object(status, "es_request", side_effect=fake):
            result = status.run_status(_cfg(), index_prefix="agent-obsv")
        self.assertEqual(result["overall"], "degraded")
        self.assertIn("index_template", result["missing"])

    def test_missing_data_stream_degrades(self) -> None:
        fake = self._make_es(
            present={"ilm/policy", "ingest/pipeline", "component_template", "index_template"},
            ds_present=False,
        )
        with mock.patch.object(status, "es_request", side_effect=fake):
            result = status.run_status(_cfg(), index_prefix="agent-obsv")
        self.assertEqual(result["overall"], "degraded")
        self.assertIn("data_stream", result["missing"])

    def test_render_text_contains_key_signals(self) -> None:
        fake = self._make_es(
            present={"ilm/policy", "ingest/pipeline", "component_template", "index_template"},
            ds_present=True,
            ds_count=7,
        )
        with mock.patch.object(status, "es_request", side_effect=fake):
            result = status.run_status(_cfg(), index_prefix="agent-obsv")
        text = status.render_text(result)
        self.assertIn("READY", text)
        self.assertIn("doc_count=7", text)


if __name__ == "__main__":
    unittest.main()
