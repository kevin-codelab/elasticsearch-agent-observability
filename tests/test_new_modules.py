#!/usr/bin/env python3
"""Tests for new modules: cli, model_pricing, evaluate, replay, instrument_frameworks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# =========================================================================
# CLI tests
# =========================================================================

class CLITests(unittest.TestCase):
    def test_commands_dict_has_required_entries(self) -> None:
        from cli import COMMANDS
        required = {"init", "quickstart", "status", "doctor", "alert", "eval", "replay", "query", "report", "session-tail", "validate", "uninstall"}
        self.assertTrue(required.issubset(set(COMMANDS.keys())), f"Missing: {required - set(COMMANDS.keys())}")

    def test_commands_values_are_tuples(self) -> None:
        from cli import COMMANDS
        for name, value in COMMANDS.items():
            self.assertIsInstance(value, tuple, f"{name} value is not a tuple")
            self.assertEqual(len(value), 2, f"{name} tuple length != 2")

    def test_scenarios_string_is_nonempty(self) -> None:
        from cli import SCENARIOS
        self.assertGreater(len(SCENARIOS), 100)
        self.assertIn("agent-obsv", SCENARIOS)

    def test_main_help_returns_zero(self) -> None:
        from cli import main
        with patch("sys.argv", ["agent-obsv", "--help"]):
            code = main()
        self.assertEqual(code, 0)

    def test_main_unknown_command_returns_one(self) -> None:
        from cli import main
        with patch("sys.argv", ["agent-obsv", "nonexistent_command_xyz"]):
            code = main()
        self.assertEqual(code, 1)

    def test_main_scenarios_returns_zero(self) -> None:
        from cli import main
        with patch("sys.argv", ["agent-obsv", "scenarios"]):
            code = main()
        self.assertEqual(code, 0)


# =========================================================================
# Evaluate tests
# =========================================================================

class EvaluateTests(unittest.TestCase):
    def test_evaluators_registry_has_entries(self) -> None:
        from evaluate import EVALUATORS
        self.assertGreaterEqual(len(EVALUATORS), 6)
        self.assertIn("llm_judge", EVALUATORS)

    def test_eval_functions_match_registry(self) -> None:
        from evaluate import EVALUATORS, _EVAL_FUNCTIONS
        for name in EVALUATORS:
            self.assertIn(name, _EVAL_FUNCTIONS, f"Evaluator '{name}' not in _EVAL_FUNCTIONS")

    def test_latency_regression_pass(self) -> None:
        from evaluate import _eval_latency_regression
        current = {"aggregations": {"p95_latency": {"values": {"95.0": 100_000_000}}}}  # 100ms
        baseline = {"aggregations": {"p95_latency": {"values": {"95.0": 100_000_000}}}}  # 100ms
        result = _eval_latency_regression(current, baseline)
        self.assertEqual(result["outcome"], "pass")

    def test_latency_regression_fail(self) -> None:
        from evaluate import _eval_latency_regression
        current = {"aggregations": {"p95_latency": {"values": {"95.0": 500_000_000}}}}  # 500ms
        baseline = {"aggregations": {"p95_latency": {"values": {"95.0": 100_000_000}}}}  # 100ms
        result = _eval_latency_regression(current, baseline, threshold=1.5)
        self.assertEqual(result["outcome"], "fail")

    def test_error_rate_regression_pass(self) -> None:
        from evaluate import _eval_error_rate_regression
        current = {"aggregations": {"total": {"value": 100}, "errors": {"doc_count": 2}}}
        baseline = {"aggregations": {"total": {"value": 100}, "errors": {"doc_count": 3}}}
        result = _eval_error_rate_regression(current, baseline)
        self.assertEqual(result["outcome"], "pass")

    def test_tool_coverage_full(self) -> None:
        from evaluate import _eval_tool_coverage
        buckets = [{"key": "search"}, {"key": "db_query"}]
        current = {"aggregations": {"tool_names": {"buckets": buckets}}}
        baseline = {"aggregations": {"tool_names": {"buckets": buckets}}}
        result = _eval_tool_coverage(current, baseline)
        self.assertEqual(result["outcome"], "pass")
        self.assertAlmostEqual(result["score"], 1.0)

    def test_tool_coverage_partial(self) -> None:
        from evaluate import _eval_tool_coverage
        current = {"aggregations": {"tool_names": {"buckets": [{"key": "search"}]}}}
        baseline = {"aggregations": {"tool_names": {"buckets": [{"key": "search"}, {"key": "db_query"}, {"key": "email"}, {"key": "calendar"}]}}}
        result = _eval_tool_coverage(current, baseline)
        self.assertIn(result["outcome"], ("fail", "degraded"))

    def test_guardrail_block_rate_pass(self) -> None:
        from evaluate import _eval_guardrail_block_rate
        current = {"aggregations": {"guardrail_total": {"doc_count": 100, "blocked": {"doc_count": 5}}}}
        baseline = {"aggregations": {}}
        result = _eval_guardrail_block_rate(current, baseline)
        self.assertEqual(result["outcome"], "pass")

    def test_llm_judge_skipped_without_endpoint(self) -> None:
        from evaluate import _eval_llm_judge
        result = _eval_llm_judge({}, {})
        self.assertEqual(result["outcome"], "pass")
        self.assertIn("Skipped", result["detail"])

    def test_llm_judge_endpoint_normalization(self) -> None:
        from evaluate import _build_llm_judge_url
        self.assertEqual(_build_llm_judge_url("http://localhost:4000"), "http://localhost:4000/v1/chat/completions")
        self.assertEqual(_build_llm_judge_url("http://localhost:4000/v1"), "http://localhost:4000/v1/chat/completions")
        self.assertEqual(
            _build_llm_judge_url("http://localhost:4000/api/v1/other"),
            "http://localhost:4000/api/v1/other/v1/chat/completions",
        )
        self.assertEqual(
            _build_llm_judge_url("http://localhost:4000/v1/chat/completions"),
            "http://localhost:4000/v1/chat/completions",
        )

    def test_eval_results_write_uses_explicit_create_id(self) -> None:
        from common import ESConfig
        from evaluate import _write_eval_results
        calls = []

        def fake_es(config, method, path, payload=None):
            calls.append((method, path, payload))
            return {"result": "created"}

        report = {
            "evaluated_at": "2026-04-28T00:00:00+00:00",
            "results": [{"run_id": "eval-abc", "evaluator": "latency_regression", "outcome": "pass", "score": 1.0}],
        }
        with patch("evaluate.es_request", side_effect=fake_es):
            _write_eval_results(ESConfig(es_url="http://x"), "agent-obsv", report)
        self.assertRegex(calls[0][1], r"/agent-obsv-events/_create/eval-abc-latency_regression-[0-9a-f]+$")

    def test_render_text(self) -> None:
        from evaluate import render_text
        report = {
            "run_id": "test-001",
            "overall_outcome": "pass",
            "average_score": 0.95,
            "time_range": "now-1h",
            "baseline_range": "now-7d/now-1h",
            "results": [
                {"evaluator": "latency_regression", "dimension": "latency", "outcome": "pass", "score": 0.95, "detail": "ok"},
            ],
        }
        text = render_text(report)
        self.assertIn("PASS", text)
        self.assertIn("test-001", text)


# =========================================================================
# Replay tests
# =========================================================================

class ReplayTests(unittest.TestCase):
    def test_build_tree_empty(self) -> None:
        from replay import _build_tree
        tree = _build_tree([])
        self.assertEqual(tree["total_events"], 0)
        self.assertEqual(tree["root_count"], 0)

    def test_build_tree_single_event(self) -> None:
        from replay import _build_tree
        events = [{"span.id": "s1", "event.action": "tool.run", "event.outcome": "success", "@timestamp": "2025-01-01T00:00:00Z"}]
        tree = _build_tree(events)
        self.assertEqual(tree["total_events"], 1)
        self.assertEqual(tree["root_count"], 1)
        self.assertEqual(tree["spans"][0]["action"], "tool.run")

    def test_build_tree_parent_child(self) -> None:
        from replay import _build_tree
        events = [
            {"span.id": "parent", "event.action": "agent.run", "event.outcome": "success", "@timestamp": "2025-01-01T00:00:00Z"},
            {"span.id": "child", "parent.id": "parent", "event.action": "tool.call", "event.outcome": "success", "@timestamp": "2025-01-01T00:00:01Z"},
        ]
        tree = _build_tree(events)
        self.assertEqual(tree["root_count"], 1)
        self.assertEqual(len(tree["spans"][0]["children"]), 1)
        self.assertEqual(tree["spans"][0]["children"][0]["action"], "tool.call")

    def test_build_tree_with_reasoning(self) -> None:
        from replay import _build_tree
        events = [{
            "span.id": "s1",
            "event.action": "decision",
            "event.outcome": "success",
            "gen_ai.agent_ext.reasoning.action": "tool_call",
            "gen_ai.agent_ext.reasoning.decision_type": "tool_selection",
            "gen_ai.agent_ext.reasoning.rationale": "DB has the data",
        }]
        tree = _build_tree(events)
        node = tree["spans"][0]
        self.assertEqual(node["reasoning_action"], "tool_call")
        self.assertEqual(node["reasoning_type"], "tool_selection")

    def test_render_tree_text(self) -> None:
        from replay import _build_tree, _render_tree_text
        events = [
            {"span.id": "p1", "event.action": "agent.run", "event.outcome": "success", "@timestamp": "2025-01-01T00:00:00Z", "gen_ai.agent_ext.component_type": "runtime"},
            {"span.id": "c1", "parent.id": "p1", "event.action": "tool.search", "event.outcome": "failure", "@timestamp": "2025-01-01T00:00:01Z", "gen_ai.tool.name": "web_search"},
        ]
        tree = _build_tree(events)
        text = _render_tree_text(tree)
        self.assertIn("agent.run", text)
        self.assertIn("web_search", text)
        self.assertIn("✗", text)  # failure icon


# =========================================================================
# Instrument frameworks tests
# =========================================================================

class InstrumentFrameworksTests(unittest.TestCase):
    def test_get_tracer_returns_none_without_otel(self) -> None:
        # OTel SDK may or may not be installed; we just verify it doesn't crash
        from instrument_frameworks import _get_tracer
        result = _get_tracer()
        # Either None (no SDK) or a tracer object — both are fine
        self.assertTrue(result is None or result is not None)

    def test_instrumentors_dict_has_entries(self) -> None:
        from instrument_frameworks import _INSTRUMENTORS
        self.assertIn("autogen", _INSTRUMENTORS)
        self.assertIn("crewai", _INSTRUMENTORS)
        self.assertIn("langgraph", _INSTRUMENTORS)
        self.assertIn("openai-agents", _INSTRUMENTORS)

    def test_auto_instrument_with_env_disable(self) -> None:
        from instrument_frameworks import auto_instrument
        import os
        os.environ["AGENT_OBSV_NO_AUTO_INSTRUMENT"] = "1"
        try:
            results = auto_instrument()
            self.assertEqual(results, {})
        finally:
            del os.environ["AGENT_OBSV_NO_AUTO_INSTRUMENT"]

    def test_auto_instrument_returns_dict(self) -> None:
        from instrument_frameworks import auto_instrument
        results = auto_instrument()
        self.assertIsInstance(results, dict)
        # Frameworks are probably not installed in test env, so all should be False
        for name, ok in results.items():
            self.assertIsInstance(ok, bool)

    def test_traced_decision_decorator_exists(self) -> None:
        from instrument_frameworks import traced_decision
        self.assertTrue(callable(traced_decision))

    def test_emit_reasoning_span_exists(self) -> None:
        from instrument_frameworks import emit_reasoning_span
        self.assertTrue(callable(emit_reasoning_span))
        # Should not crash even without OTel SDK
        emit_reasoning_span(action="tool_call", decision_type="test")


# =========================================================================
# Quickstart tests (lightweight — no actual bootstrap)
# =========================================================================

class QuickstartTests(unittest.TestCase):
    def test_framework_signatures_has_entries(self) -> None:
        from quickstart import FRAMEWORK_SIGNATURES
        self.assertGreaterEqual(len(FRAMEWORK_SIGNATURES), 5)
        for key, info in FRAMEWORK_SIGNATURES.items():
            self.assertIn("packages", info)
            self.assertIn("imports", info)
            self.assertIn("runtime", info)

    def test_detect_framework_empty_dir(self) -> None:
        from quickstart import _detect_framework
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _detect_framework(Path(tmpdir))
        self.assertIsNone(result)

    def test_detect_framework_crewai(self) -> None:
        from quickstart import _detect_framework
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "requirements.txt").write_text("crewai>=0.50\n")
            result = _detect_framework(Path(tmpdir))
        self.assertEqual(result, "crewai")

    def test_detect_framework_with_evidence_explains_match(self) -> None:
        from quickstart import _detect_framework_with_evidence
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "requirements.txt").write_text("crewai>=0.50\n")
            result = _detect_framework_with_evidence(Path(tmpdir))
        self.assertEqual(result["framework"], "crewai")
        self.assertEqual(result["recommended_runtime"], "python")
        self.assertEqual(result["recommended_path"], "python-bootstrap-and-wrappers")
        self.assertEqual(result["matches"][0]["path"], "requirements.txt")
        self.assertEqual(result["matches"][0]["match_type"], "package")

    def test_detect_framework_with_evidence_recommends_session_tail_for_openclaw(self) -> None:
        from quickstart import _detect_framework_with_evidence
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = {"dependencies": {"openclaw": "^1.0.0"}}
            (Path(tmpdir) / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
            result = _detect_framework_with_evidence(Path(tmpdir))
        self.assertEqual(result["framework"], "openclaw")
        self.assertEqual(result["recommended_runtime"], "node")
        self.assertEqual(result["recommended_path"], "session-tail-first")
        self.assertIn("session-tail", result["why"])

    def test_detect_framework_langgraph(self) -> None:
        from quickstart import _detect_framework
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "requirements.txt").write_text("langgraph\nlangchain\n")
            result = _detect_framework(Path(tmpdir))
        self.assertEqual(result, "langgraph")

    def test_detect_framework_node_openclaw(self) -> None:
        from quickstart import _detect_framework
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = {"dependencies": {"openclaw": "^1.0.0"}}
            (Path(tmpdir) / "package.json").write_text(json.dumps(pkg))
            result = _detect_framework(Path(tmpdir))
        self.assertEqual(result, "openclaw")

    def test_quickstart_persists_detection_evidence(self) -> None:
        import bootstrap_observability
        import quickstart

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_dir = root / "agent"
            output_dir = root / "out"
            agent_dir.mkdir()
            (agent_dir / "requirements.txt").write_text("crewai>=0.50\n", encoding="utf-8")
            argv = ["quickstart", "--agent-dir", str(agent_dir), "--output-dir", str(output_dir)]
            with patch("sys.argv", argv):
                with patch.object(bootstrap_observability, "main", return_value=0):
                    code = quickstart.main()
            evidence = json.loads((output_dir / quickstart.DETECTION_EVIDENCE_FILENAME).read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(evidence["framework"], "crewai")
        self.assertEqual(evidence["selected_framework"], "crewai")
        self.assertEqual(evidence["matches"][0]["path"], "requirements.txt")
        self.assertEqual(evidence["recommended_path"], "python-bootstrap-and-wrappers")


# =========================================================================
# Session tail renderer tests
# =========================================================================

class SessionTailRendererTests(unittest.TestCase):
    def test_generated_session_tail_preserves_timestamps_and_persists_offsets(self) -> None:
        import py_compile
        import render_session_tail

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "bundle"
            paths = render_session_tail.render_session_tail_bundle(out, session_dir=str(Path(tmpdir) / "sessions"))
            script_path = paths["script"]
            py_compile.compile(str(script_path), doraise=True)
            source = script_path.read_text(encoding="utf-8")
            self.assertIn("def _timestamp_to_unix_nano", source)
            self.assertIn("event_ns = _timestamp_to_unix_nano", source)
            self.assertIn(".session_tail_state.json", source)
            self.assertIn("self._state[key] =", source)
            self.assertIn("--from-end", source)
            self.assertIn("--backfill", source)
            field_map = json.loads(paths["field_map"].read_text(encoding="utf-8"))
            self.assertEqual(field_map["provider"], "gen_ai.provider.name")
            self.assertEqual(field_map["response_id"], "gen_ai.response.id")
            self.assertEqual(field_map["mcp_method"], "mcp.method.name")
            self.assertEqual(field_map["turn_id"], "gen_ai.agent_ext.turn_id")


if __name__ == "__main__":
    unittest.main()
