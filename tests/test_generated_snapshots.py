"""Snapshot digests for generated observability assets.

These tests guard the repo's main value: rendered Elasticsearch/Kibana assets,
instrumentation snippets, and session-tail bundles. If a digest changes, review
the generated output before updating the expected value.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import render_es_assets  # noqa: E402
import render_instrument_snippet  # noqa: E402
import render_session_tail  # noqa: E402

DISCOVERY = {
    "files_scanned": 12,
    "detected_modules": [
        {"module_kind": "tool_registry"},
        {"module_kind": "model_adapter"},
        {"module_kind": "mcp_surface"},
    ],
    "recommended_ingest_modes": [{"mode": "collector", "score": 0.94}],
}
MODULES = ["mcp_surface", "model_adapter", "tool_registry"]

EXPECTED_DIGESTS = {
    "alert_rule_specs": "00acac41ed0f2989fdfeb06f1098d3fc27ba53b6c8e42331847322bd151a2b6f",
    "component_template_ecs_base": "68e179bbdb2c661153a30516db795eae75765c10a75381c581cb78efd8fafbbb",
    "index_template": "9719b3e5ab7fb2f031e707a5a63d1a980ecf8077fdd4c24116cb64d6875bf392",
    "ingest_pipeline": "26f780f60ac8724aa871eba569ae48ab686d8fc3e852aced5ccb3c69244e6de0",
    "investigation_queries": "5fbf6befadd53cf7942e22ea5422e63a8ec5d81bd84dc33c41e968251c81e8c1",
    "kibana_saved_objects": "da24e99b32a9890238aaf53f45e5302bbd8811ccfcd1a568b96a7f4bfe835d96",
    "node_instrumentation": "20717f8a96fabc77e2789442ad9c577ad330015546094a72f21d9bc2a60ff7c3",
    "python_instrumentation": "e86d56c10516b253c954566d65b8af8f82165a2dfeb0b1c2fa4f0350ce9de198",
    "report_config": "0bcd132323bd88310b53518197f476d40003566388ec4c7ccae476d72514ae70",
    "session_tail_field_map": "aa9fa280110aa785c4335e232d3188cf9fe41533846128a36ca11bf15fa3164d",
    "session_tail_readme": "dd4d674075f3a009e25fb3b5c89c4d54262f26c72e35bc118c20170a02be9c38",
    "session_tail_script": "d2f05dfa8937c1617d268a232f6476ccf4f43c8998cd8df977d15f2ef0157c01",
}


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_json(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _digest_text(canonical)


def _current_digests() -> dict[str, str]:
    digests = {
        "component_template_ecs_base": _digest_json(render_es_assets.build_component_template_ecs_base("agent-obsv")),
        "index_template": _digest_json(render_es_assets.build_index_template("agent-obsv", MODULES)),
        "ingest_pipeline": _digest_json(render_es_assets.build_ingest_pipeline(MODULES)),
        "kibana_saved_objects": _digest_json(render_es_assets.build_kibana_saved_objects("agent-obsv")),
        "investigation_queries": _digest_json(render_es_assets.build_investigation_queries("agent-obsv")),
        "alert_rule_specs": _digest_json(render_es_assets.build_alert_rule_specs("agent-obsv")),
        "report_config": _digest_json(render_es_assets.build_report_config("agent-obsv", DISCOVERY)),
        "python_instrumentation": _digest_text(
            render_instrument_snippet.render_instrument_snippet(
                DISCOVERY,
                service_name="agent-runtime",
                environment="dev",
                otlp_endpoint="http://127.0.0.1:4317",
                index_prefix="agent-obsv",
            )
        ),
        "node_instrumentation": _digest_text(
            render_instrument_snippet.render_node_snippet(
                service_name="agent-runtime",
                environment="dev",
                otlp_endpoint="http://127.0.0.1:4317",
            )
        ),
    }
    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = render_session_tail.render_session_tail_bundle(
            Path(tmp_dir),
            bridge_url="http://127.0.0.1:14319",
            session_dir="/var/lib/agent/sessions",
            service_name="agent-runtime",
        )
        digests["session_tail_script"] = _digest_text(paths["script"].read_text(encoding="utf-8"))
        digests["session_tail_field_map"] = _digest_text(paths["field_map"].read_text(encoding="utf-8"))
        digests["session_tail_readme"] = _digest_text(paths["readme"].read_text(encoding="utf-8"))
    return digests


class GeneratedAssetSnapshotTests(unittest.TestCase):
    def test_generated_asset_digests_match_reviewed_snapshots(self) -> None:
        self.assertEqual(_current_digests(), EXPECTED_DIGESTS)

    def test_generated_assets_keep_critical_semantics(self) -> None:
        pipeline = render_es_assets.build_ingest_pipeline(MODULES)
        removed_fields = {proc["remove"]["field"] for proc in pipeline["processors"] if "remove" in proc}
        for field in {
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.system_instructions",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "tool_args",
            "tool_result",
        }:
            self.assertIn(field, removed_fields)

        kibana = render_es_assets.build_kibana_saved_objects("agent-obsv")
        self.assertIn("trace_timeline_id", kibana["summary"])
        self.assertIn("mcp_search_id", kibana["summary"])
        self.assertGreaterEqual(len(kibana["summary"]["lens_ids"]), 20)

        investigations = render_es_assets.build_investigation_queries("agent-obsv")
        self.assertTrue(any("mcp.method.name" in item["query"] for item in investigations["queries"]))
        self.assertTrue(all("COUNT(*) WHERE" not in item["query"] for item in investigations["queries"]))

        alert_specs = render_es_assets.build_alert_rule_specs("agent-obsv")
        self.assertTrue(any("not event.dataset:internal.*" in rule["query"] for rule in alert_specs["rules"]))

        python_snippet = render_instrument_snippet.render_instrument_snippet(
            DISCOVERY,
            service_name="agent-runtime",
            environment="dev",
            otlp_endpoint="http://127.0.0.1:4317",
            index_prefix="agent-obsv",
        )
        for token in ("gen_ai.operation.name", "mcp.method.name", "gen_ai.response.id", "gen_ai.usage.cache_read.input_tokens"):
            self.assertIn(token, python_snippet)

    def test_session_tail_bundle_keeps_semantic_field_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = render_session_tail.render_session_tail_bundle(Path(tmp_dir), service_name="agent-runtime")
            field_map = json.loads(paths["field_map"].read_text(encoding="utf-8"))
        self.assertEqual(field_map["mcp_method"], "mcp.method.name")
        self.assertEqual(field_map["response_id"], "gen_ai.response.id")
        self.assertEqual(field_map["cache_read_input_tokens"], "gen_ai.usage.cache_read.input_tokens")
        self.assertEqual(field_map["operation_name"], "gen_ai.operation.name")


if __name__ == "__main__":
    unittest.main()
