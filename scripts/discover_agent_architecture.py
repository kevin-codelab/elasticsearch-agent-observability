#!/usr/bin/env python3
"""Discover monitorable modules from an agent workspace."""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import SkillError, iter_text_files, normalize_text, print_error, read_text_file, safe_relative, utcnow_iso, write_json

_CONTENT_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _content_match(keyword: str, text: str) -> bool:
    pattern = _CONTENT_PATTERN_CACHE.get(keyword)
    if pattern is None and keyword not in _CONTENT_PATTERN_CACHE:
        if any(ch in keyword for ch in (" ", "(", "_", "/", ".", "-")):
            _CONTENT_PATTERN_CACHE[keyword] = None
            return keyword in text
        escaped = re.escape(keyword)
        pattern = re.compile(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])")
        _CONTENT_PATTERN_CACHE[keyword] = pattern
    if pattern is None:
        return keyword in text
    return bool(pattern.search(text))


MODULE_RULES = {
    "agent_manifest": {
        "priority": 100,
        "path_keywords": ["skill.md", "agents.md"],
        "content_keywords": ["name:", "description:", "resolve the script path"],
        "signals": ["runs", "turns", "errors", "config_changes"],
    },
    "runtime_entrypoint": {
        "priority": 98,
        "path_keywords": ["agent.py", "main.py", "cli.py", "app.py", "server.py", "store.py"],
        "content_keywords": ["if __name__ == \"__main__\"", "argparse.argumentparser", "main()"],
        "signals": ["runs", "turns", "latency", "errors"],
    },
    "command_surface": {
        "priority": 92,
        "path_keywords": ["cli", "command"],
        "content_keywords": ["add_parser(", "add_subparsers", "cmd_", "click.command", "typer"],
        "signals": ["command_calls", "command_errors", "command_latency"],
    },
    "workflow_orchestrator": {
        "priority": 90,
        "path_keywords": ["workflow", "graph", "pipeline", "planner", "task"],
        "content_keywords": ["workflow", "pipeline", "planner", "task", "stage"],
        "signals": ["workflow_steps", "task_latency", "task_failures"],
    },
    "tool_registry": {
        "priority": 92,
        "path_keywords": ["tool", "tools"],
        "content_keywords": ["tool", "tool call", "execute_command", "mcp_call_tool", "function_call"],
        "signals": ["tool_calls", "tool_latency", "tool_errors", "tool_args_redacted"],
    },
    "model_adapter": {
        "priority": 90,
        "path_keywords": ["llm", "model", "openai", "anthropic", "prompt"],
        "content_keywords": ["openai", "anthropic", "model", "completion", "messages", "token", "go-openai", "sashabaranov", "langchain", "llamaindex"],
        "signals": ["model_calls", "token_usage", "cost", "latency", "model_errors"],
    },
    "memory_store": {
        "priority": 85,
        "path_keywords": ["memory", "store", "cache", "retrieval", "vector"],
        "content_keywords": ["memory", "cache", "retrieval", "index.json", "meta_path", "snapshot"],
        "signals": ["cache_hits", "cache_misses", "retrieval_latency", "sync_events"],
    },
    "mcp_surface": {
        "priority": 88,
        "path_keywords": ["mcp"],
        "content_keywords": ["mcp", "jsonrpc", "tools/call", "session_id", "mcp.method.name"],
        "signals": ["mcp_calls", "mcp_latency", "mcp_errors", "session_events"],
    },
    "evaluation_harness": {
        "priority": 75,
        "path_keywords": ["eval", "evaluation", "benchmark"],
        "content_keywords": ["evaluation", "score", "benchmark", "scenario"],
        "signals": ["evaluation_runs", "scores", "regressions"],
    },
    "existing_observability": {
        "priority": 78,
        "path_keywords": ["trace", "telemetry", "observability", "metric", "logging"],
        "content_keywords": ["trace", "telemetry", "metric", "observability", "otlp", "opentelemetry"],
        "signals": ["reuse_existing_telemetry"],
    },
    "otel_sdk_surface": {
        "priority": 87,
        "path_keywords": ["otel", "opentelemetry"],
        "content_keywords": ["otlp", "opentelemetry", "tracerprovider", "meterprovider", "otelloghandler"],
        "signals": ["otlp_ingest", "otel_semantics", "trace_bridge"],
    },
    "elastic_apm": {
        "priority": 86,
        "path_keywords": ["apm", "elasticapm"],
        "content_keywords": ["elasticapm", "elastic apm", "transaction", "capture_span", "apm server"],
        "signals": ["apm_spans", "transactions", "errors"],
    },
    "elastic_agent": {
        "priority": 84,
        "path_keywords": ["elastic-agent", "fleet", "beats"],
        "content_keywords": ["fleet", "elastic-agent", "enrollment token", "policy id"],
        "signals": ["fleet_enrollment", "agent_policy", "host_metrics"],
    },
    "browser_frontend": {
        "priority": 83,
        "path_keywords": ["package.json", "src/main.", "app.tsx", "app.jsx", "pages/", "frontend"],
        "content_keywords": [
            "@elastic/apm-rum",
            "reactdom.createroot",
            "react-router",
            "vue-router",
            "next/router",
            "window.location.pathname",
        ],
        "signals": ["page_loads", "route_changes", "frontend_errors", "web_vitals", "frontend_trace_correlation"],
    },
    "web_service": {
        "priority": 83,
        "path_keywords": ["routes/", "api/", "server.ts", "server.js", "main.py", "app.py", "main.go"],
        "content_keywords": [
            "fastapi",
            "flask",
            "django",
            "express(",
            "koa(",
            "http.createserver",
            "apirouter",
            "@app.route",
            "router.get(",
            "router.post(",
            "uvicorn.run",
            "gin.default()",
            "http.listenandserve",
            "actix_web",
            "axum",
            "@restcontroller",
            "@requestmapping",
            "spring",
        ],
        "signals": ["http_requests", "route_latency", "backend_errors", "distributed_tracing"],
    },
    "guardrail": {
        "priority": 82,
        "path_keywords": ["guardrail", "safety", "content_filter", "moderation"],
        "content_keywords": [
            "guardrail",
            "content_filter",
            "moderation",
            "prompt_injection",
            "content_safety",
            "safety_check",
            "block_response",
            "redact",
        ],
        "signals": ["guardrail_checks", "guardrail_blocks", "guardrail_latency", "safety_events"],
    },
    "knowledge_base": {
        "priority": 82,
        "path_keywords": ["knowledge", "rag", "retrieval", "embedding", "vector"],
        "content_keywords": [
            "knowledge_base",
            "retrieval_augmented",
            "embedding",
            "vector_search",
            "similarity_search",
            "chroma",
            "pinecone",
            "weaviate",
            "qdrant",
            "faiss",
        ],
        "signals": ["retrieval_calls", "retrieval_latency", "retrieval_scores", "embedding_latency"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover monitorable agent modules")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-files", type=int, default=400)
    return parser.parse_args()


def score_rule(path_text: str, content_text: str, rule: dict[str, Any]) -> int:
    score = 0
    for keyword in rule["path_keywords"]:
        if keyword in path_text:
            score += 5
    for keyword in rule["content_keywords"]:
        if _content_match(keyword, content_text):
            score += 3
    return score


def detect_command_handlers(content: str) -> list[str]:
    return sorted(set(re.findall(r"def\s+(cmd_[a-zA-Z0-9_]+)\s*\(", content)))


def build_architecture_style(module_kinds: list[str], command_handlers: list[str]) -> str:
    kinds = set(module_kinds)
    if "agent_manifest" in kinds and "runtime_entrypoint" in kinds and command_handlers:
        return "single-script skill with command surface"
    if "workflow_orchestrator" in kinds and "tool_registry" in kinds:
        return "multi-stage orchestrated agent"
    return "mixed agent workspace"


def recommend_modules(detected_modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations = []
    for module in detected_modules:
        recommendations.append(
            {
                "module_id": module["module_id"],
                "module_kind": module["module_kind"],
                "priority": module["priority"],
                "signals": module["signals"],
                "why": f"Detected from {len(module['evidence_files'])} evidence file(s) with score {module['score']}",
            }
        )
    recommendations.sort(key=lambda item: (-item["priority"], item["module_id"]))
    return recommendations


def recommend_ingest_modes(detected_modules: list[dict[str, Any]], recommended_signals: list[str]) -> list[dict[str, Any]]:
    kinds = {module.get("module_kind") for module in detected_modules}
    signals = set(recommended_signals)
    recommendations = [
        {
            "mode": "collector",
            "score": 0.94,
            "why": "Safest default path. Works well when the runtime can emit OTLP without Elastic-native enrollment.",
            "prerequisites": ["OTLP endpoint reachable", "Collector binary available"],
        }
    ]
    if {"existing_observability", "otel_sdk_surface", "elastic_apm", "browser_frontend", "web_service"} & kinds or {"otlp_ingest", "trace_bridge", "frontend_trace_correlation", "distributed_tracing"} & signals:
        recommendations.append(
            {
                "mode": "apm-otlp-hybrid",
                "score": 0.88,
                "why": "Best fit when the workspace already speaks OTLP, APM, frontend RUM, or backend HTTP services and you want Elastic-native semantics without dropping Collector support.",
                "prerequisites": ["APM endpoint or Fleet policy available", "OTLP exporter or SDK hooks present"],
            }
        )
    if {"runtime_entrypoint", "tool_registry", "elastic_agent"} & kinds:
        recommendations.append(
            {
                "mode": "elastic-agent-fleet",
                "score": 0.82,
                "why": "Best fit when operators prefer managed enrollment, host telemetry, and Fleet-governed policies.",
                "prerequisites": ["Fleet Server available", "Enrollment token available"],
            }
        )
    recommendations.sort(key=lambda item: (-item["score"], item["mode"]))
    return recommendations


def compute_maturity_score(detected_modules: list[dict[str, Any]], command_handlers: list[str], recommended_signals: list[str]) -> dict[str, Any]:
    """Compute an observability maturity score (0-100) for the workspace.

    Scoring dimensions:
    - basic_logging (0-15): runtime entrypoint + basic log output
    - structured_telemetry (0-25): OTel SDK, trace, existing observability hooks
    - genai_instrumentation (0-25): model adapter, tool registry, token/cost signals
    - operational_readiness (0-20): MCP surface, command handlers, evaluation harness
    - depth_bonus (0-15): breadth of signals, command handler diversity
    """
    kinds = {m.get("module_kind") for m in detected_modules}
    signals = set(recommended_signals)

    # Dimension 1: basic logging
    basic = 0
    if "runtime_entrypoint" in kinds:
        basic += 8
    if "agent_manifest" in kinds:
        basic += 4
    if any(s in signals for s in ("runs", "errors", "turns")):
        basic += 3

    # Dimension 2: structured telemetry
    telemetry = 0
    if "existing_observability" in kinds:
        telemetry += 10
    if "otel_sdk_surface" in kinds:
        telemetry += 10
    if "elastic_apm" in kinds:
        telemetry += 5
    if any(s in signals for s in ("otlp_ingest", "otel_semantics", "trace_bridge")):
        telemetry += 5
    telemetry = min(telemetry, 25)

    # Dimension 3: GenAI instrumentation
    genai = 0
    if "model_adapter" in kinds:
        genai += 10
    if "tool_registry" in kinds:
        genai += 8
    if any(s in signals for s in ("token_usage", "cost")):
        genai += 4
    if any(s in signals for s in ("tool_latency", "tool_errors")):
        genai += 3
    genai = min(genai, 25)

    # Dimension 4: operational readiness
    ops = 0
    if "mcp_surface" in kinds:
        ops += 7
    if "command_surface" in kinds or command_handlers:
        ops += 5
    if "evaluation_harness" in kinds:
        ops += 4
    if "memory_store" in kinds:
        ops += 4
    ops = min(ops, 20)

    # Dimension 5: depth bonus
    depth = 0
    depth += min(len(signals), 10)
    depth += min(len(command_handlers), 5)
    depth = min(depth, 15)

    total = basic + telemetry + genai + ops + depth

    if total >= 80:
        level = "advanced"
        guidance = "Full trace/metric/log pipeline is ready. Focus on custom dashboards, alerting thresholds, and cost optimization."
    elif total >= 50:
        level = "intermediate"
        guidance = "Core signals exist. Recommend adding spanmetrics, token budget tracking, and structured error categorization."
    elif total >= 25:
        level = "basic"
        guidance = "Basic runtime detected. Recommend integrating OTel SDK, adding tool call tracing, and enabling model token tracking."
    else:
        level = "minimal"
        guidance = "Very limited observability surface. Start with runtime entrypoint instrumentation and basic OTLP export."

    return {
        "score": total,
        "level": level,
        "guidance": guidance,
        "dimensions": {
            "basic_logging": basic,
            "structured_telemetry": telemetry,
            "genai_instrumentation": genai,
            "operational_readiness": ops,
            "depth_bonus": depth,
        },
    }


def discover_workspace(workspace: Path, max_files: int = 400) -> dict[str, Any]:
    files = iter_text_files(workspace, max_files=max_files)
    aggregate: dict[str, dict[str, Any]] = {}
    command_handlers: list[str] = []
    all_signals: set[str] = set()
    evidence_by_kind: dict[str, list[str]] = defaultdict(list)

    for path in files:
        relative_path = safe_relative(path, workspace).lower()
        try:
            raw_content = read_text_file(path)
        except SkillError:
            continue
        content = normalize_text(raw_content).lower()
        command_handlers.extend(detect_command_handlers(raw_content))
        for module_kind, rule in MODULE_RULES.items():
            score = score_rule(relative_path, content, rule)
            if score <= 0:
                continue
            module = aggregate.setdefault(
                module_kind,
                {
                    "module_id": module_kind,
                    "module_kind": module_kind,
                    "score": 0,
                    "priority": rule["priority"],
                    "signals": list(rule["signals"]),
                    "evidence_files": [],
                    "notes": [],
                },
            )
            module["score"] += score
            relative = safe_relative(path, workspace)
            if relative not in module["evidence_files"]:
                module["evidence_files"].append(relative)
            evidence_by_kind[module_kind].append(relative)
            all_signals.update(rule["signals"])

    if command_handlers:
        aggregate["command_handlers"] = {
            "module_id": "command_handlers",
            "module_kind": "command_handlers",
            "score": len(command_handlers) * 4,
            "priority": 89,
            "signals": ["command_calls", "command_errors", "command_latency"],
            "evidence_files": sorted(set(evidence_by_kind.get("runtime_entrypoint", []))),
            "notes": command_handlers,
        }
        all_signals.update(["command_calls", "command_errors", "command_latency"])

    detected_modules = sorted(aggregate.values(), key=lambda item: (-item["priority"], -item["score"]))
    recommended_signals = sorted(all_signals)
    payload = {
        "workspace": str(workspace),
        "generated_at": utcnow_iso(),
        "files_scanned": len(files),
        "architecture_style": build_architecture_style([m["module_kind"] for m in detected_modules], command_handlers),
        "detected_modules": detected_modules,
        "command_handlers": sorted(set(command_handlers)),
        "recommended_monitoring_plan": recommend_modules(detected_modules),
        "recommended_signals": recommended_signals,
        "recommended_ingest_modes": recommend_ingest_modes(detected_modules, recommended_signals),
        "maturity_score": compute_maturity_score(detected_modules, sorted(set(command_handlers)), recommended_signals),
    }
    return payload


def main() -> int:
    try:
        args = parse_args()
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.exists():
            raise SkillError(f"Workspace not found: {workspace}")
        output = Path(args.output).expanduser().resolve()
        payload = discover_workspace(workspace, max_files=args.max_files)
        write_json(output, payload)
        print(f"✅ architecture discovery written: {output}")
        print(f"   modules: {len(payload['detected_modules'])}")
        print(f"   architecture style: {payload['architecture_style']}")
        maturity = payload.get("maturity_score", {})
        print(f"   maturity: {maturity.get('score', 0)}/100 ({maturity.get('level', 'unknown')})")
        return 0
    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to discover agent architecture: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
