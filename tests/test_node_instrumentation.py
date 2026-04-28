"""Node.js / TypeScript instrumentation bundle tests."""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import render_instrument_snippet  # noqa: E402


class NodeInstrumentationTests(unittest.TestCase):
    def test_render_node_snippet_contains_expected_wiring(self) -> None:
        snippet = render_instrument_snippet.render_node_snippet(
            service_name="openclaw-agent",
            environment="dev",
            otlp_endpoint="http://127.0.0.1:4317",
        )
        self.assertIn("@opentelemetry/sdk-node", snippet)
        self.assertIn("OTEL_SERVICE_NAME", snippet)
        self.assertIn("openclaw-agent", snippet)
        self.assertIn("tracedToolCall", snippet)
        self.assertIn("tracedModelCall", snippet)
        self.assertIn("tracedMcpToolCall", snippet)
        self.assertIn("execute_tool", snippet)
        self.assertIn("mcp.method.name", snippet)
        self.assertIn("gen_ai.response.id", snippet)
        self.assertIn("gen_ai.usage.input_tokens", snippet)

    def test_render_snippet_to_file_node_mode_writes_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir) / "agent_otel_bootstrap.py"
            result = render_instrument_snippet.render_snippet_to_file(
                {"languages": ["typescript"], "detected_modules": []},
                out,
                service_name="openclaw-agent",
                environment="dev",
                otlp_endpoint="http://127.0.0.1:4317",
                index_prefix="agent-obsv",
                runtime="node",
            )
            self.assertIsInstance(result, dict)
            self.assertTrue(result["snippet"].name.endswith(".mjs"))
            self.assertTrue(result["readme"].exists())
            snippet_text = result["snippet"].read_text(encoding="utf-8")
            self.assertIn("NodeSDK", snippet_text)

    def test_render_snippet_to_file_auto_picks_node_for_ts_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir) / "agent_otel_bootstrap.py"
            discovery = {
                "languages": ["TypeScript"],
                "detected_modules": [{"path": "src/index.ts", "module_kind": "runtime_entrypoint"}],
            }
            result = render_instrument_snippet.render_snippet_to_file(
                discovery,
                out,
                service_name="openclaw-agent",
                environment="dev",
                otlp_endpoint="http://127.0.0.1:4317",
                index_prefix="agent-obsv",
                runtime="auto",
            )
            self.assertIsInstance(result, dict)
            self.assertTrue((result["snippet"].parent / "README.md").exists())

    def test_render_snippet_to_file_auto_falls_back_to_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir) / "agent_otel_bootstrap.py"
            result = render_instrument_snippet.render_snippet_to_file(
                {"languages": ["Python"], "detected_modules": []},
                out,
                service_name="svc",
                environment="dev",
                otlp_endpoint="http://127.0.0.1:4317",
                index_prefix="agent-obsv",
                runtime="auto",
            )
            self.assertEqual(result, out)
            self.assertIn("opentelemetry", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
