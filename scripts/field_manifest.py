#!/usr/bin/env python3
"""Machine-readable field tiers for instrumentation coverage.

Keep this small: it is the shared manifest for `doctor.py` and contract tests,
not a replacement for the full telemetry schema.
"""

from __future__ import annotations

from typing import Any


TIER2_FIELDS: dict[str, dict[str, Any]] = {
    "gen_ai.tool.name": {
        "tier": 2,
        "label": "tool name",
        "powers": "tool-level latency/error panels, error_rate_spike diagnosis",
        "fix": '@traced_tool_call("my_tool")\ndef my_tool(...):\n    ...',
    },
    "gen_ai.conversation.id": {
        "tier": 2,
        "label": "session / conversation ID",
        "powers": "session_failure_hotspot alert, session drill-down",
        "fix": 'span.set_attribute("gen_ai.conversation.id", session_id)',
    },
    "gen_ai.agent_ext.turn_id": {
        "tier": 2,
        "label": "turn ID",
        "powers": "long_turn_hotspot alert, turn-level diffing",
        "fix": 'span.set_attribute("gen_ai.agent_ext.turn_id", turn_id)',
    },
    "gen_ai.agent_ext.component_type": {
        "tier": 2,
        "label": "component type",
        "powers": "per-component dashboards, every alert filter",
        "fix": 'span.set_attribute("gen_ai.agent_ext.component_type", "tool")  # or llm/mcp/memory/knowledge/guardrail/runtime',
    },
    "gen_ai.operation.name": {
        "tier": 2,
        "label": "OTel GenAI operation",
        "powers": "operation mix panels, GenAI semconv compatibility",
        "fix": 'span.set_attribute("gen_ai.operation.name", "chat")  # or embeddings/retrieval/invoke_agent/execute_tool',
    },
}


TIER3_FIELDS: dict[str, dict[str, Any]] = {
    "gen_ai.provider.name": {
        "tier": 3,
        "label": "GenAI provider",
        "powers": "provider-level routing and model diagnostics",
        "fix": 'span.set_attribute("gen_ai.provider.name", "openai")  # or anthropic/aws.bedrock/gcp.vertex_ai',
    },
    "gen_ai.request.model": {
        "tier": 3,
        "label": "request model",
        "powers": "model distribution, model latency, token analysis",
        "fix": 'span.set_attribute("gen_ai.request.model", model_name)',
    },
    "gen_ai.usage.input_tokens": {
        "tier": 3,
        "label": "input tokens",
        "powers": "token usage trend and token spike investigations",
        "fix": 'span.set_attribute("gen_ai.usage.input_tokens", input_tokens)',
    },
    "gen_ai.usage.output_tokens": {
        "tier": 3,
        "label": "output tokens",
        "powers": "token usage trend and token spike investigations",
        "fix": 'span.set_attribute("gen_ai.usage.output_tokens", output_tokens)',
    },
    "mcp.method.name": {
        "tier": 3,
        "label": "MCP method",
        "powers": "MCP tool-call drilldown and alert grouping",
        "fix": 'span.set_attribute("mcp.method.name", "tools/call")',
    },
    "gen_ai.evaluation.name": {
        "tier": 3,
        "label": "OTel evaluation name",
        "powers": "standard GenAI evaluation event compatibility",
        "fix": 'span.set_attribute("gen_ai.evaluation.name", "relevance")',
    },
    "gen_ai.agent_ext.retry_count": {
        "tier": 3,
        "label": "retry count",
        "powers": "retry_storm alert",
        "fix": 'span.set_attribute("gen_ai.agent_ext.retry_count", retry_count)',
    },
    "error.type": {
        "tier": 3,
        "label": "error type classification",
        "powers": "diagnosis phrasing (timeout vs application-level)",
        "fix": 'span.set_attribute("error.type", "timeout")  # or rate_limit/api_error/auth_error/tool_error',
    },
    "gen_ai.agent_ext.reasoning.action": {
        "tier": 3,
        "label": "reasoning trace",
        "powers": "reasoning panels, session trace decision trail",
        "fix": 'emit_reasoning_span(action="tool_call", decision_type="tool_selection", rationale="...")',
    },
    "gen_ai.feedback.score": {
        "tier": 3,
        "label": "user feedback",
        "powers": "feedback sentiment/score panels",
        "fix": 'curl -X POST http://127.0.0.1:14319/v1/feedback -d \'{"score":1,"trace_id":"..."}\'',
    },
}


FIELD_MANIFEST: dict[str, dict[str, Any]] = {
    **TIER2_FIELDS,
    **TIER3_FIELDS,
}
