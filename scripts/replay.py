#!/usr/bin/env python3
"""Session replay — reconstruct a nested span tree from ES trace data.

Given a session (conversation) ID or trace ID, queries ES for all events,
reconstructs the parent-child span tree, and outputs a structured replay
in text-tree or JSON format.

Usage:
    python scripts/replay.py --es-url <url> --session-id <id>
    python scripts/replay.py --es-url <url> --trace-id <id> --format json
    agent-obsv replay --es-url <url> --session-id <id>
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from common import (
    ESConfig,
    SkillError,
    build_data_stream_name,
    es_request,
    print_error,
    validate_credential_pair,
    validate_index_prefix,
)


def _query_events(
    config: ESConfig,
    ds_name: str,
    *,
    session_id: str = "",
    trace_id: str = "",
    max_events: int = 500,
) -> list[dict[str, Any]]:
    """Fetch all events for a session or trace."""
    filters: list[dict[str, Any]] = []
    if session_id:
        filters.append({"term": {"gen_ai.conversation.id": session_id}})
    elif trace_id:
        filters.append({"term": {"trace.id": trace_id}})
    else:
        raise SkillError("Either --session-id or --trace-id is required")

    payload = {
        "size": max_events,
        "query": {"bool": {"filter": filters}},
        "sort": [{"@timestamp": "asc"}, {"gen_ai.agent_ext.reasoning.step_index": "asc"}],
        "_source": True,
    }
    result = es_request(config, "POST", f"/{ds_name}*/_search", payload)
    return [h.get("_source", {}) for h in (result.get("hits") or {}).get("hits", [])]


def _build_tree(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a nested span tree from flat events using span.id / parent.id."""
    nodes: dict[str, dict[str, Any]] = {}
    roots: list[str] = []

    for evt in events:
        span_id = evt.get("span.id", "")
        if not span_id:
            # Events without span.id go to a synthetic root
            span_id = f"_evt_{id(evt)}"
        node = {
            "span_id": span_id,
            "parent_id": evt.get("parent.id", ""),
            "timestamp": evt.get("@timestamp", ""),
            "action": evt.get("event.action", ""),
            "outcome": evt.get("event.outcome", ""),
            "tool": evt.get("gen_ai.tool.name", ""),
            "model": evt.get("gen_ai.request.model", ""),
            "component": evt.get("gen_ai.agent_ext.component_type", ""),
            "turn_id": evt.get("gen_ai.agent_ext.turn_id", ""),
            "latency_ms": evt.get("gen_ai.agent_ext.latency_ms"),
            "duration_ns": evt.get("event.duration"),
            "message": str(evt.get("message", ""))[:200],
            # Reasoning trace
            "reasoning_action": evt.get("gen_ai.agent_ext.reasoning.action", ""),
            "reasoning_type": evt.get("gen_ai.agent_ext.reasoning.decision_type", ""),
            "reasoning_rationale": evt.get("gen_ai.agent_ext.reasoning.rationale", ""),
            "reasoning_confidence": evt.get("gen_ai.agent_ext.reasoning.confidence"),
            "reasoning_alternatives": evt.get("gen_ai.agent_ext.reasoning.alternatives", ""),
            # Feedback
            "feedback_score": evt.get("gen_ai.feedback.score"),
            "feedback_sentiment": evt.get("gen_ai.feedback.sentiment", ""),
            # Agent info
            "agent_name": evt.get("gen_ai.agent.name", ""),
            "agent_id": evt.get("gen_ai.agent.id", ""),
            "children": [],
        }
        nodes[span_id] = node

    # Link children
    for nid, node in nodes.items():
        pid = node["parent_id"]
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        else:
            roots.append(nid)

    return {
        "root_count": len(roots),
        "total_events": len(events),
        "spans": [nodes[r] for r in roots],
    }


def _render_tree_text(tree: dict[str, Any]) -> str:
    """Render the span tree as an indented text tree."""
    lines: list[str] = []
    lines.append(f"Session replay: {tree['total_events']} events, {tree['root_count']} root span(s)")
    lines.append("")

    def _render_node(node: dict[str, Any], depth: int = 0) -> None:
        indent = "  " * depth
        icon = "✓" if node["outcome"] == "success" else ("✗" if node["outcome"] == "failure" else "·")

        # Main line
        parts = [f"{indent}{icon}"]
        if node["timestamp"]:
            parts.append(f"[{node['timestamp']}]")
        if node["action"]:
            parts.append(node["action"])
        if node["component"]:
            parts.append(f"({node['component']})")
        if node["tool"]:
            parts.append(f"tool={node['tool']}")
        if node["model"]:
            parts.append(f"model={node['model']}")
        if node["agent_name"]:
            parts.append(f"agent={node['agent_name']}")

        duration_text = ""
        if node["latency_ms"]:
            duration_text = f"{node['latency_ms']:.0f}ms"
        elif node["duration_ns"]:
            duration_text = f"{node['duration_ns'] / 1e6:.0f}ms"
        if duration_text:
            parts.append(duration_text)

        lines.append(" ".join(parts))

        # Reasoning trace (if present)
        if node["reasoning_action"]:
            r_parts = [f"{indent}  💭"]
            r_parts.append(f"decided={node['reasoning_action']}")
            if node["reasoning_type"]:
                r_parts.append(f"type={node['reasoning_type']}")
            if node["reasoning_confidence"] is not None:
                r_parts.append(f"conf={node['reasoning_confidence']}")
            if node["reasoning_alternatives"]:
                r_parts.append(f"rejected=[{node['reasoning_alternatives']}]")
            lines.append(" ".join(r_parts))
            if node["reasoning_rationale"]:
                lines.append(f"{indent}  📝 {node['reasoning_rationale'][:150]}")

        # Feedback (if present)
        if node["feedback_score"] is not None or node["feedback_sentiment"]:
            fb_parts = [f"{indent}  👤"]
            if node["feedback_score"] is not None:
                fb_parts.append(f"score={node['feedback_score']}")
            if node["feedback_sentiment"]:
                fb_parts.append(f"sentiment={node['feedback_sentiment']}")
            lines.append(" ".join(fb_parts))

        # Message snippet
        if node["message"] and node["action"] not in ("otlp.log", "otlp.span"):
            msg = node["message"][:120].replace("\n", " ")
            lines.append(f"{indent}  → {msg}")

        # Children
        for child in node["children"]:
            _render_node(child, depth + 1)

    for root in tree["spans"]:
        _render_node(root)
        lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Session replay — nested span tree from ES traces")
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--es-user", default="")
    parser.add_argument("--es-password", default="")
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--session-id", default="", help="Conversation / session ID to replay")
    parser.add_argument("--trace-id", default="", help="Trace ID to replay")
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--no-verify-tls", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if not args.session_id and not args.trace_id:
            print("Error: either --session-id or --trace-id is required", file=sys.stderr)
            return 1

        credentials = validate_credential_pair(args.es_user, args.es_password)
        config = ESConfig(
            es_url=args.es_url,
            es_user=credentials[0] if credentials else None,
            es_password=credentials[1] if credentials else None,
            verify_tls=not args.no_verify_tls,
        )
        index_prefix = validate_index_prefix(args.index_prefix)
        ds_name = build_data_stream_name(index_prefix)

        events = _query_events(
            config, ds_name,
            session_id=args.session_id,
            trace_id=args.trace_id,
            max_events=args.max_events,
        )

        if not events:
            print("No events found for the given session/trace.", file=sys.stderr)
            return 2

        tree = _build_tree(events)

        if args.format == "json":
            print(json.dumps(tree, ensure_ascii=False, indent=2))
        else:
            print(_render_tree_text(tree))

        return 0

    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:
        print_error(f"Replay failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
