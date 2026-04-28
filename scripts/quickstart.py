#!/usr/bin/env python3
"""Guided setup for common agent frameworks.

Detects the agent framework, generates the right instrumentation bundle,
renders ES/Collector/Bridge assets, and optionally applies them.

Usage:
    agent-obsv quickstart --agent-dir /path/to/agent
    agent-obsv quickstart --agent-dir /path/to/agent --framework crewai --es-url http://localhost:9200 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import (
    SkillError,
    ensure_dir,
    print_error,
    validate_workspace_dir,
    write_json,
    write_text,
)

DETECTION_EVIDENCE_FILENAME = "quickstart-detection.json"

# Supported framework detection patterns.
FRAMEWORK_SIGNATURES: dict[str, dict[str, Any]] = {
    "autogen": {
        "packages": ["pyautogen", "autogen-agentchat", "autogen"],
        "imports": ["autogen", "autogen.agentchat"],
        "label": "AutoGen",
        "runtime": "python",
    },
    "crewai": {
        "packages": ["crewai"],
        "imports": ["crewai"],
        "label": "CrewAI",
        "runtime": "python",
    },
    "langgraph": {
        "packages": ["langgraph", "langchain"],
        "imports": ["langgraph", "langchain"],
        "label": "LangGraph / LangChain",
        "runtime": "python",
    },
    "openai-agents": {
        "packages": ["openai-agents", "agents"],
        "imports": ["agents"],
        "label": "OpenAI Agents SDK",
        "runtime": "python",
    },
    "llamaindex": {
        "packages": ["llama-index", "llama_index"],
        "imports": ["llama_index"],
        "label": "LlamaIndex",
        "runtime": "python",
    },
    "openclaw": {
        "packages": ["openclaw"],
        "imports": ["openclaw"],
        "label": "OpenClaw",
        "runtime": "node",
    },
    "mastra": {
        "packages": ["@mastra/core"],
        "imports": ["@mastra"],
        "label": "Mastra",
        "runtime": "node",
    },
}


def _detect_framework_with_evidence(agent_dir: Path) -> dict[str, Any]:
    """Scan agent_dir for framework signatures and explain the match."""
    matches: list[dict[str, str]] = []

    def _record(framework: str, source: str, path: Path, match_type: str, value: str) -> None:
        matches.append(
            {
                "framework": framework,
                "label": FRAMEWORK_SIGNATURES[framework]["label"],
                "runtime": FRAMEWORK_SIGNATURES[framework]["runtime"],
                "source": source,
                "path": str(path.relative_to(agent_dir)),
                "match_type": match_type,
                "value": value,
            }
        )

    # Check Python requirements
    for req_file in ("requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile"):
        path = agent_dir / req_file
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").lower()
            except OSError:
                continue
            for fw_key, fw_info in FRAMEWORK_SIGNATURES.items():
                for pkg in fw_info["packages"]:
                    if pkg.lower() in content:
                        _record(fw_key, "python-dependency", path, "package", pkg)
                        return _build_detection_result(matches)

    # Check Node.js package.json
    pkg_json = agent_dir / "package.json"
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            dep_names = {k.lower() for k in deps}
            for fw_key, fw_info in FRAMEWORK_SIGNATURES.items():
                for pkg_name in fw_info["packages"]:
                    if pkg_name.lower() in dep_names:
                        _record(fw_key, "node-dependency", pkg_json, "package", pkg_name)
                        return _build_detection_result(matches)
        except (OSError, json.JSONDecodeError):
            pass

    # Fallback: scan Python imports in .py files
    py_files = list(agent_dir.rglob("*.py"))[:100]
    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for fw_key, fw_info in FRAMEWORK_SIGNATURES.items():
            for imp in fw_info["imports"]:
                if f"import {imp}" in content or f"from {imp}" in content:
                    _record(fw_key, "python-import", py_file, "import", imp)
                    return _build_detection_result(matches)

    return _build_detection_result(matches)


def _build_detection_result(matches: list[dict[str, str]]) -> dict[str, Any]:
    if not matches:
        return {
            "framework": None,
            "matches": [],
            "recommended_runtime": "python",
            "recommended_path": "generic-bootstrap",
            "why": "No known framework signature matched dependency or import scans.",
        }
    selected = matches[0]
    framework = selected["framework"]
    runtime = selected["runtime"]
    if framework == "openclaw":
        recommended_path = "session-tail-first"
        why = "OpenClaw often has session JSONL or non-standard providers; session-tail is the least invasive first path."
    elif runtime == "node":
        recommended_path = "node-preload-and-wrappers"
        why = "Detected a Node/TypeScript framework; use preload for HTTP spans and wrappers for GenAI semantic fields."
    else:
        recommended_path = "python-bootstrap-and-wrappers"
        why = "Detected a Python framework; use the generated bootstrap plus wrappers for tool/model call sites."
    return {
        "framework": framework,
        "matches": matches,
        "recommended_runtime": runtime,
        "recommended_path": recommended_path,
        "why": why,
    }


def _detect_framework(agent_dir: Path) -> str | None:
    """Scan agent_dir for framework signatures. Returns framework key or None."""
    result = _detect_framework_with_evidence(agent_dir)
    framework = result.get("framework")
    return str(framework) if framework else None


def _manual_detection_result(framework: str) -> dict[str, Any]:
    fw_info = FRAMEWORK_SIGNATURES.get(framework, {})
    runtime = fw_info.get("runtime", "python")
    if framework == "openclaw":
        recommended_path = "session-tail-first"
    elif runtime == "node":
        recommended_path = "node-preload-and-wrappers"
    else:
        recommended_path = "python-bootstrap-and-wrappers"
    return {
        "framework": framework,
        "matches": [],
        "recommended_runtime": runtime,
        "recommended_path": recommended_path,
        "why": "User override via --framework; no auto-detection evidence was used.",
        "source": "user_override",
    }


def _persist_detection_evidence(output_dir: Path, detection: dict[str, Any], *, selected_framework: str, agent_dir: Path) -> Path:
    payload = dict(detection)
    payload["selected_framework"] = selected_framework
    payload["agent_dir"] = str(agent_dir)
    path = output_dir / DETECTION_EVIDENCE_FILENAME
    write_json(path, payload)
    return path


def _print_detection_explanation(detection: dict[str, Any]) -> None:
    framework = detection.get("framework")
    matches = detection.get("matches") or []
    if not framework:
        print(f"   why: {detection.get('why')}")
        return
    print(f"   why: {detection.get('why')}")
    for match in matches[:3]:
        print(
            "   evidence: "
            f"{match['source']} `{match['path']}` matched {match['match_type']} `{match['value']}`"
        )
    print(f"   recommended path: {detection.get('recommended_path')}")


def _generate_framework_guide(framework: str, output_dir: Path) -> Path:
    """Generate a framework-specific instrumentation guide."""
    guides: dict[str, str] = {
        "autogen": _GUIDE_AUTOGEN,
        "crewai": _GUIDE_CREWAI,
        "langgraph": _GUIDE_LANGGRAPH,
        "openai-agents": _GUIDE_OPENAI_AGENTS,
        "llamaindex": _GUIDE_LLAMAINDEX,
        "openclaw": _GUIDE_OPENCLAW,
        "mastra": _GUIDE_MASTRA,
    }
    guide_text = guides.get(framework, _GUIDE_GENERIC)
    guide_path = output_dir / "INSTRUMENTATION_GUIDE.md"
    write_text(guide_path, guide_text)
    return guide_path


_GUIDE_AUTOGEN = """\
# AutoGen Instrumentation Guide

## Step 1: Install OTel SDK
```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc traceloop-sdk
```

## Step 2: Initialize tracing (add to your entrypoint)
```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-autogen-agent")
```

Or use the generated `agent_otel_bootstrap.py`:
```python
import agent_otel_bootstrap  # auto-setup on import
```

## Step 3: Add semantic annotations to your agents
```python
from agent_otel_bootstrap import traced_tool_call, traced_model_call

# Wrap tool functions
@traced_tool_call("web_search")
def search(query: str) -> str:
    ...

# AutoGen's GroupChat and ConversableAgent already emit model calls
# if traceloop-sdk is initialized. For custom tools, use the wrapper.
```

## Step 4: Set conversation tracking
```python
import opentelemetry.context as ctx
from opentelemetry import trace

tracer = trace.get_tracer("autogen-agent")
with tracer.start_as_current_span("session", attributes={
    "gen_ai.conversation.id": session_id,
    "gen_ai.agent_ext.component_type": "runtime",
}):
    chat_result = user_proxy.initiate_chat(assistant, message=task)
```
"""

_GUIDE_CREWAI = """\
# CrewAI Instrumentation Guide

## Step 1: Install OTel SDK
```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc traceloop-sdk
```

## Step 2: Initialize tracing (add before crew.kickoff())
```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-crewai-crew")
```

## Step 3: CrewAI auto-instrumentation
CrewAI + traceloop-sdk automatically traces:
- Agent → LLM calls (model name, tokens, latency)
- Tool executions (tool name, success/failure)
- Task delegation between agents

For custom tools, add explicit annotations:
```python
from crewai.tools import BaseTool
from agent_otel_bootstrap import traced_tool_call

class MyTool(BaseTool):
    name = "my_tool"

    @traced_tool_call("my_tool")
    def _run(self, query: str) -> str:
        ...
```

## Step 4: Multi-agent correlation
```python
# CrewAI automatically sets gen_ai.conversation.id per crew execution.
# To correlate across crews, set it explicitly:
crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    verbose=True,
)
# The execution trace appears as a single session in Kibana.
```
"""

_GUIDE_LANGGRAPH = """\
# LangGraph / LangChain Instrumentation Guide

## Step 1: Install OTel SDK
```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc traceloop-sdk
```

## Step 2: Initialize tracing
```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-langgraph-agent")
```

## Step 3: LangChain auto-instrumentation
traceloop-sdk auto-patches LangChain's LLM/Chat/Embedding calls.
LangGraph state transitions are captured as nested spans.

For custom tools:
```python
from langchain.tools import tool
from agent_otel_bootstrap import traced_tool_call

@tool
@traced_tool_call("search_docs")
def search_docs(query: str) -> str:
    \"\"\"Search documentation.\"\"\"
    ...
```

## Step 4: Graph-level tracking
```python
from langgraph.graph import StateGraph

# Each graph.invoke() creates a trace.
# Set gen_ai.conversation.id in the config for session correlation:
result = graph.invoke(
    {"messages": [HumanMessage(content="...")]},
    config={"configurable": {"thread_id": session_id}},
)
```
"""

_GUIDE_OPENAI_AGENTS = """\
# OpenAI Agents SDK Instrumentation Guide

## Step 1: Install OTel SDK
```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

## Step 2: Use the generated bootstrap
```python
import agent_otel_bootstrap  # auto-patches OpenAI SDK on import
```

## Step 3: Agent SDK traces
The auto-patch in `agent_otel_bootstrap.py` monkey-patches
`openai.resources.chat.completions.Completions.create`, so every
`Runner.run()` call chain is traced with model name, token usage, and latency.

For tool functions:
```python
from agents import function_tool
from agent_otel_bootstrap import traced_tool_call

@function_tool
@traced_tool_call("get_weather")
def get_weather(city: str) -> str:
    ...
```
"""

_GUIDE_LLAMAINDEX = """\
# LlamaIndex Instrumentation Guide

## Step 1: Install
```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc traceloop-sdk
```

## Step 2: Initialize
```python
from traceloop.sdk import Traceloop
Traceloop.init(app_name="my-llamaindex-agent")
```

## Step 3: Auto-instrumentation
traceloop-sdk auto-instruments LlamaIndex's LLM, embedding, and retrieval calls.
RAG pipeline spans include:
- `gen_ai.operation.name: "retrieval"` for vector search
- `gen_ai.agent_ext.component_type: "knowledge"` for knowledge base access
- Token usage per LLM call
"""

_GUIDE_OPENCLAW = """\
# OpenClaw Instrumentation Guide

OpenClaw is a TypeScript-first agent framework. Three paths:

## Path A: Session Tail (recommended)
OpenClaw writes per-session JSONL files with complete model call data. Tail
these files and ship to ES — zero code changes, most reliable for non-standard
LLM providers (e.g. gongfeng).
```bash
# Generate the session tail bundle
agent-obsv session-tail \\
  --output-dir ./generated/session-tail \\
  --session-dir /path/to/openclaw/sessions \\
  --bridge-url http://localhost:14319

# Start tailing (runs alongside OpenClaw, only new records by default)
python generated/session-tail/session_tail.py --from-end
```
Edit `field_map.json` to match your JSONL field names if they differ from defaults.

## Path B: LLM Proxy
If OpenClaw uses a standard OpenAI-compatible endpoint (not gongfeng), put a
tracing proxy in front:
```bash
agent-obsv init --workspace /path/to/openclaw --output-dir ./generated \\
  --generate-llm-proxy --es-url http://localhost:9200 --apply-es-assets
cd generated/llm-proxy && docker compose up -d
```
Then set in your OpenClaw config:
```
OPENAI_API_BASE=http://localhost:4000/v1
```

## Path C: Node.js instrumentation
```bash
agent-obsv init --workspace /path/to/openclaw --output-dir ./generated \\
  --generate-instrument-snippet --instrument-runtime node
```
Then preload the bootstrap:
```bash
node --import ./generated/node-instrumentation/agent-otel-bootstrap.mjs dist/index.js
```
Note: this only intercepts standard OpenAI/Anthropic SDK calls. If OpenClaw
uses a custom LLM provider, Path A (session tail) is more reliable.
"""

_GUIDE_MASTRA = """\
# Mastra Instrumentation Guide

Mastra is a TypeScript agent framework. Use the Node.js instrumentation path:

```bash
agent-obsv init --workspace /path/to/mastra-app --output-dir ./generated \\
  --generate-instrument-snippet --instrument-runtime node \\
  --es-url http://localhost:9200 --apply-es-assets
```

Preload in your entrypoint:
```bash
node --import ./generated/node-instrumentation/agent-otel-bootstrap.mjs dist/index.js
```
"""

_GUIDE_GENERIC = """\
# Generic Agent Instrumentation Guide

## Step 1: Install OTel SDK
```bash
# Python
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc

# Node.js
npm i @opentelemetry/sdk-node @opentelemetry/exporter-trace-otlp-grpc
```

## Step 2: Use the generated bootstrap snippet
The `agent_otel_bootstrap.py` (or `.mjs`) file handles TracerProvider,
MeterProvider, and LogHandler setup. Import it at your entrypoint.

## Step 3: Add semantic annotations
Wrap tool/model/agent/MCP calls with the convenience wrappers. They emit OTel GenAI fields such as `gen_ai.provider.name`, `gen_ai.response.id`, `gen_ai.operation.name`, and `mcp.method.name`.

## Step 4: Investigate in Elastic
Use `generated/investigation-queries.json` for ES|QL drilldowns and `generated/alert-rule-specs.json` as Kibana Query Rule templates.

See `references/post_bootstrap_playbook.md` for the full self-extension checklist.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guided setup for common agent frameworks",
    )
    parser.add_argument("--agent-dir", required=True, help="Path to the agent project")
    parser.add_argument("--output-dir", default="", help="Output directory (defaults to <agent-dir>/generated/observability)")
    parser.add_argument(
        "--framework",
        choices=list(FRAMEWORK_SIGNATURES.keys()) + ["auto"],
        default="auto",
        help="Agent framework (default: auto-detect)",
    )
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--es-user", default="")
    parser.add_argument("--es-password", default="")
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--service-name", default="")
    parser.add_argument("--apply", action="store_true", help="Apply ES/Kibana assets to the cluster after rendering")
    parser.add_argument("--kibana-url", default="")
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument("--generate-llm-proxy", action="store_true", help="Also generate the LLM proxy bundle (no agent code changes when compatible)")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        agent_dir = validate_workspace_dir(Path(args.agent_dir))
        output_dir = Path(args.output_dir or (agent_dir / "generated" / "observability")).expanduser().resolve()

        # --- Detect framework ---
        framework = args.framework
        if framework == "auto":
            detection = _detect_framework_with_evidence(agent_dir)
            detected = detection.get("framework")
            if detected:
                fw_info = FRAMEWORK_SIGNATURES[detected]
                framework = str(detected)
                print(f"🔍 Detected framework: {fw_info['label']}")
                _print_detection_explanation(detection)
            else:
                framework = "generic"
                print("🔍 No known framework detected; using generic setup.")
                _print_detection_explanation(detection)
        else:
            detection = _manual_detection_result(framework)
            fw_info = FRAMEWORK_SIGNATURES.get(framework, {})
            print(f"📦 Framework: {fw_info.get('label', framework)}")
            runtime_hint = fw_info.get("runtime", "python")
            print(f"   why: user override via --framework; runtime path `{runtime_hint}`")

        ensure_dir(output_dir)
        detection_path = _persist_detection_evidence(output_dir, detection, selected_framework=framework, agent_dir=agent_dir)
        print(f"   🧭 Detection evidence: {detection_path}")

        # --- Generate framework-specific guide ---
        guide_path = _generate_framework_guide(framework, output_dir)
        print(f"   📖 Instrumentation guide: {guide_path}")

        # --- Determine runtime ---
        fw_info = FRAMEWORK_SIGNATURES.get(framework, {})
        runtime = fw_info.get("runtime", "python")

        # --- Determine service name ---
        service_name = args.service_name or agent_dir.name

        # --- Build bootstrap command ---
        bootstrap_args = [
            "--workspace", str(agent_dir),
            "--output-dir", str(output_dir),
            "--es-url", args.es_url,
            "--index-prefix", args.index_prefix,
            "--service-name", service_name,
            "--generate-instrument-snippet",
            "--instrument-runtime", runtime,
        ]
        if args.es_user:
            bootstrap_args.extend(["--es-user", args.es_user])
        if args.es_password:
            bootstrap_args.extend(["--es-password", args.es_password])
        if args.apply:
            bootstrap_args.append("--apply-es-assets")
            if args.kibana_url:
                bootstrap_args.extend(["--kibana-url", args.kibana_url, "--apply-kibana-assets"])
        if args.no_verify_tls:
            bootstrap_args.append("--no-verify-tls")
        if args.generate_llm_proxy:
            bootstrap_args.append("--generate-llm-proxy")

        # --- Run bootstrap ---
        print()
        print(f"🚀 Running bootstrap for {service_name} ...")
        print()

        # Import and invoke bootstrap directly
        import bootstrap_observability
        original_argv = sys.argv
        sys.argv = ["agent-obsv quickstart"] + bootstrap_args
        try:
            exit_code = bootstrap_observability.main()
        finally:
            sys.argv = original_argv

        if exit_code == 0:
            print()
            print("=" * 60)
            print("✅ Quickstart complete!")
            print()
            print("Next steps:")
            print(f"  1. Read the instrumentation guide: {guide_path}")
            if framework in FRAMEWORK_SIGNATURES:
                print(f"  2. Follow the {FRAMEWORK_SIGNATURES[framework]['label']}-specific steps")
            print(f"  3. Run `agent-obsv doctor --es-url {args.es_url}` to verify the pipeline")
            print("  4. Open Kibana to see the dashboard")
            print("=" * 60)

        return exit_code

    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:
        print_error(f"Quickstart failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
