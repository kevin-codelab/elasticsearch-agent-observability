#!/usr/bin/env python3
"""Render Elasticsearch 9.x assets for agent observability.

Upgraded to use data streams, ECS-compatible mappings, component templates,
tiered ILM, structured ingest parsing, and Lens visualizations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import (
    SkillError,
    build_component_template_name,
    build_data_stream_name,
    build_events_alias,
    ensure_dir,
    print_error,
    read_json,
    validate_index_prefix,
    validate_positive_int,
    write_json,
    write_text,
)

DEFAULT_KIBANA_COLUMNS = [
    "@timestamp",
    "gen_ai.conversation.id",
    "gen_ai.agent.id",
    "gen_ai.agent_ext.turn_id",
    "gen_ai.agent_ext.component_type",
    "event.action",
    "service.name",
    "gen_ai.tool.name",
    "gen_ai.request.model",
    "gen_ai.operation.name",
    "gen_ai.agent_ext.latency_ms",
    "event.outcome",
    "error.type",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Elasticsearch assets")
    parser.add_argument("--discovery", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--dashboard-extensions", default="", help="Optional YAML/JSON file declaring extra dashboard panels")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# ECS-compatible field mappings
# ---------------------------------------------------------------------------

def _ecs_base_properties() -> dict[str, Any]:
    """ECS base + agent-observability custom fields using ECS naming."""
    return {
        # --- ECS base ---
        "@timestamp": {"type": "date"},
        "message": {"type": "text"},
        "event.action": {"type": "keyword"},
        "event.category": {"type": "keyword"},
        "event.kind": {"type": "keyword"},
        "event.outcome": {"type": "keyword"},
        "event.duration": {"type": "long", "doc_values": True},
        "event.module": {"type": "keyword"},
        "event.dataset": {"type": "keyword"},
        # --- service / agent ---
        "service.name": {"type": "keyword"},
        "service.version": {"type": "keyword"},
        "service.environment": {"type": "keyword"},
        "agent.id": {"type": "keyword"},
        "agent.name": {"type": "keyword"},
        "agent.type": {"type": "keyword"},
        # --- trace / span ---
        "trace.id": {"type": "keyword"},
        "span.id": {"type": "keyword"},
        "parent.id": {"type": "keyword"},
        "transaction.id": {"type": "keyword"},
        # --- observer (this product) ---
        "observer.product": {"type": "keyword"},
        "observer.type": {"type": "keyword"},
        "observer.version": {"type": "keyword"},
        "observer.ingest_error": {"type": "keyword"},
        # --- host (for Elastic Agent host metrics) ---
        "host.name": {"type": "keyword"},
        "host.hostname": {"type": "keyword"},
        "host.os.platform": {"type": "keyword"},
        # --- labels ---
        "labels.recommended_modules": {"type": "keyword"},
        "labels.ingest_mode": {"type": "keyword"},
        "labels.unmapped": {"type": "flattened"},
        "labels.payload_truncated": {"type": "boolean"},
        # --- gen_ai (OpenTelemetry GenAI Semantic Conventions v1.40+) ---
        "gen_ai.system": {"type": "keyword"},
        "gen_ai.provider.name": {"type": "keyword"},
        "gen_ai.request.model": {"type": "keyword"},
        "gen_ai.response.model": {"type": "keyword"},
        "gen_ai.response.id": {"type": "keyword"},
        "gen_ai.response.finish_reasons": {"type": "keyword"},
        "gen_ai.output.type": {"type": "keyword"},
        "gen_ai.operation.name": {"type": "keyword"},
        "gen_ai.data_source.id": {"type": "keyword"},
        "gen_ai.usage.input_tokens": {"type": "long"},
        "gen_ai.usage.output_tokens": {"type": "long"},
        "gen_ai.usage.total_tokens": {"type": "long"},
        "gen_ai.usage.cache_read.input_tokens": {"type": "long"},
        "gen_ai.usage.cache_creation.input_tokens": {"type": "long"},
        "gen_ai.token.type": {"type": "keyword"},
        # --- gen_ai OTel standard: agent + tool + conversation ---
        "gen_ai.agent.id": {"type": "keyword"},
        "gen_ai.agent.name": {"type": "keyword"},
        "gen_ai.agent.version": {"type": "keyword"},
        "gen_ai.agent.description": {"type": "text"},
        "gen_ai.conversation.id": {"type": "keyword"},
        "gen_ai.prompt.name": {"type": "keyword"},
        "gen_ai.tool.name": {"type": "keyword"},
        "gen_ai.tool.call.id": {"type": "keyword"},
        # --- Model Context Protocol (OTel MCP semantic conventions) ---
        "mcp.method.name": {"type": "keyword"},
        "mcp.session.id": {"type": "keyword"},
        "mcp.resource.uri": {"type": "keyword"},
        "error.type": {"type": "keyword"},
        # --- agent_ext: project extensions awaiting OTel SemConv proposal ---
        "gen_ai.agent_ext.turn_id": {"type": "keyword"},
        "gen_ai.agent_ext.component_type": {"type": "keyword"},  # runtime / tool / llm / mcp / memory / knowledge / guardrail
        "gen_ai.agent_ext.retry_count": {"type": "integer"},
        "gen_ai.agent_ext.latency_ms": {"type": "float"},
        "gen_ai.agent_ext.module": {"type": "keyword"},
        "gen_ai.agent_ext.module_kind": {"type": "keyword"},
        "gen_ai.agent_ext.semantic_kind": {"type": "keyword"},
        "gen_ai.agent_ext.verify_id": {"type": "keyword"},
        # --- memory / knowledge monitoring (agent_ext) ---
        "gen_ai.agent_ext.retrieval_latency_ms": {"type": "float"},
        "gen_ai.agent_ext.cache_hit": {"type": "boolean"},
        "gen_ai.agent_ext.retrieval_score": {"type": "float"},
        "gen_ai.agent_ext.knowledge_source": {"type": "keyword"},
        # --- guardrail / safety monitoring ---
        "gen_ai.guardrail.action": {"type": "keyword"},  # pass / block / redact
        "gen_ai.guardrail.rule_id": {"type": "keyword"},
        "gen_ai.guardrail.category": {"type": "keyword"},  # content_safety / prompt_injection / pii / custom
        "gen_ai.guardrail.latency_ms": {"type": "float"},
        # --- evaluation observability ---
        "gen_ai.evaluation.run_id": {"type": "keyword"},
        "gen_ai.evaluation.evaluator": {"type": "keyword"},
        "gen_ai.evaluation.score": {"type": "float"},
        "gen_ai.evaluation.outcome": {"type": "keyword"},  # pass / fail / degraded
        "gen_ai.evaluation.dimension": {"type": "keyword"},  # quality / safety / latency / efficiency
        "gen_ai.evaluation.name": {"type": "keyword"},
        # --- multi-agent correlation ---
        "gen_ai.agent_ext.parent_agent.id": {"type": "keyword"},
        "gen_ai.agent_ext.causality.trigger_span_id": {"type": "keyword"},
        "gen_ai.agent_ext.delegation_target": {"type": "keyword"},
        # --- reasoning trace ---
        "gen_ai.agent_ext.reasoning.action": {"type": "keyword"},         # chosen action: tool_call / delegate / respond / wait / escalate
        "gen_ai.agent_ext.reasoning.alternatives": {"type": "keyword"},   # rejected alternatives (comma-separated or array)
        "gen_ai.agent_ext.reasoning.rationale": {"type": "text"},         # free-text why-this-action explanation
        "gen_ai.agent_ext.reasoning.confidence": {"type": "float"},       # agent's self-reported confidence 0-1
        "gen_ai.agent_ext.reasoning.input_summary": {"type": "text"},     # condensed input context (NOT the raw prompt)
        "gen_ai.agent_ext.reasoning.decision_type": {"type": "keyword"},  # routing / tool_selection / delegation / termination / retry
        "gen_ai.agent_ext.reasoning.step_index": {"type": "integer"},     # ordinal within the turn (0-based)
        # --- user feedback ---
        "gen_ai.feedback.score": {"type": "float"},           # numeric score (e.g. 1-5, or -1/0/1 for thumbs)
        "gen_ai.feedback.sentiment": {"type": "keyword"},     # positive / negative / neutral
        "gen_ai.feedback.comment": {"type": "text"},          # free-text user comment
        "gen_ai.feedback.trace_id": {"type": "keyword"},      # trace.id this feedback is about
        "gen_ai.feedback.session_id": {"type": "keyword"},    # gen_ai.conversation.id this feedback is about
        "gen_ai.feedback.user_id": {"type": "keyword"},       # end-user identifier (opaque)
    }


def build_component_template_ecs_base(index_prefix: str) -> dict[str, Any]:
    return {
        "template": {
            "mappings": {
                "dynamic": "false",
                "dynamic_templates": [],
                "properties": _ecs_base_properties(),
            },
        },
        "_meta": {
            "product": "elasticsearch-agent-observability",
            "managed": True,
            "description": "ECS-compatible base mappings for agent observability data streams",
        },
    }


def build_component_template_settings(index_prefix: str, retention_days: int) -> dict[str, Any]:
    return {
        "template": {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 1,
                "index.default_pipeline": f"{index_prefix}-normalize",
                "index.lifecycle.name": f"{index_prefix}-lifecycle",
                "index.codec": "best_compression",
            },
        },
        "_meta": {
            "product": "elasticsearch-agent-observability",
            "managed": True,
            "retention_days": retention_days,
        },
    }


def build_index_template(index_prefix: str, modules: list[str]) -> dict[str, Any]:
    ds_name = build_data_stream_name(index_prefix)
    return {
        "index_patterns": [f"{ds_name}*"],
        "data_stream": {},
        "priority": 500,
        "composed_of": [
            build_component_template_name(index_prefix, "ecs-base"),
            build_component_template_name(index_prefix, "settings"),
        ],
        "_meta": {
            "product": "elasticsearch-agent-observability",
            "managed": True,
            "recommended_modules": modules,
        },
    }


# ---------------------------------------------------------------------------
# Ingest pipeline — structured parsing
# ---------------------------------------------------------------------------

def build_ingest_pipeline(modules: list[str]) -> dict[str, Any]:
    return {
        "description": "Normalize agent observability events: ECS alignment, structured parsing, GenAI field preservation, and redaction",
        "_meta": {
            "product": "elasticsearch-agent-observability",
            "managed": True,
        },
        "processors": [
            # --- ECS stamping ---
            {"set": {"field": "observer.product", "value": "elasticsearch-agent-observability"}},
            {"set": {"field": "observer.type", "value": "agent-observability"}},
            {"set": {"field": "labels.recommended_modules", "value": modules}},
            {"set": {"field": "@timestamp", "value": "{{{_ingest.timestamp}}}", "override": False}},
            {"set": {"field": "event.kind", "value": "event", "override": False}},
            {"set": {"field": "event.category", "value": "process", "override": False}},
            # --- structured log parsing (JSON body) ---
            # MUST run before field normalization so that fields inside a JSON
            # message body (e.g. {"latency_ms":120, "tool_name":"search"}) are
            # flattened to top-level before the normalizer looks for them.
            {"json": {"field": "message", "target_field": "_parsed_message", "ignore_failure": True}},
            {
                "script": {
                    "lang": "painless",
                    "source": (
                        "def known_roots = new HashSet(['@timestamp', 'message', 'event', 'service', "
                        "'agent', 'trace', 'span', 'parent', 'transaction', 'observer', 'host', "
                        "'labels', 'gen_ai', 'mcp', 'alert', 'otel', 'error', 'log']); "
                        "if (ctx._parsed_message instanceof Map) { "
                        "  Map pm = (Map) ctx._parsed_message; "
                        "  for (def e0 : pm.entrySet()) { "
                        "    String k0 = e0.getKey().toString(); "
                        "    def v0 = e0.getValue(); "
                        "    if (k0.contains('.')) { "
                        "      if (!ctx.containsKey(k0) || ctx[k0] == null) { ctx[k0] = v0; } "
                        "      continue; "
                        "    } "
                        "    if (!known_roots.contains(k0)) { "
                        "      ctx.labels = ctx.labels ?: new HashMap(); "
                        "      ctx.labels.unmapped = ctx.labels.unmapped ?: new HashMap(); "
                        "      ctx.labels.unmapped[k0] = v0; "
                        "      continue; "
                        "    } "
                        "    if (!(v0 instanceof Map)) { "
                        "      if (!ctx.containsKey(k0) || ctx[k0] == null) { ctx[k0] = v0; } "
                        "      continue; "
                        "    } "
                        "    Map m0 = (Map) v0; "
                        "    for (def e1 : m0.entrySet()) { "
                        "      String dk1 = k0 + '.' + e1.getKey().toString(); "
                        "      def v1 = e1.getValue(); "
                        "      if (!(v1 instanceof Map)) { "
                        "        if (!ctx.containsKey(dk1) || ctx[dk1] == null) { ctx[dk1] = v1; } "
                        "        continue; "
                        "      } "
                        "      Map m1 = (Map) v1; "
                        "      for (def e2 : m1.entrySet()) { "
                        "        String dk2 = dk1 + '.' + e2.getKey().toString(); "
                        "        def v2 = e2.getValue(); "
                        "        if (!(v2 instanceof Map)) { "
                        "          if (!ctx.containsKey(dk2) || ctx[dk2] == null) { ctx[dk2] = v2; } "
                        "          continue; "
                        "        } "
                        "        Map m2 = (Map) v2; "
                        "        for (def e3 : m2.entrySet()) { "
                        "          String dk3 = dk2 + '.' + e3.getKey().toString(); "
                        "          if (!ctx.containsKey(dk3) || ctx[dk3] == null) { ctx[dk3] = e3.getValue(); } "
                        "        } "
                        "      } "
                        "    } "
                        "  } "
                        "} "
                        "ctx.remove('_parsed_message');"
                    ),
                    "ignore_failure": True,
                }
            },
            # --- field normalization + derived fields (single script) ---
            # Consolidated into one Painless script to reduce compilation overhead.
            # Runs AFTER JSON body parsing so fields from message body are visible.
            #
            # Does three things in order:
            #   1. Map common non-standard field names → canonical schema
            #   2. Derive event.duration from latency_ms (ms → ns)
            #   3. Derive event.outcome from status/success/error.type
            {
                "script": {
                    "lang": "painless",
                    "source": (
                        # --- 1. field normalization ---
                        # latency
                        "def lat = ctx['latency_ms'] ?: ctx['duration_ms'] ?: ctx['latency']; "
                        "if (lat != null && ctx['gen_ai.agent_ext.latency_ms'] == null) { ctx['gen_ai.agent_ext.latency_ms'] = lat; } "
                        # tool name
                        "def tn = ctx['tool_name'] ?: ctx['tool']; "
                        "if (tn != null && ctx['gen_ai.tool.name'] == null) { ctx['gen_ai.tool.name'] = tn; } "
                        # provider / model / response
                        "def pn = ctx['provider'] ?: ctx['provider_name'] ?: ctx['gen_ai.system']; "
                        "if (pn != null && ctx['gen_ai.provider.name'] == null) { ctx['gen_ai.provider.name'] = pn; } "
                        "def mn = ctx['model'] ?: ctx['model_name']; "
                        "if (mn != null && ctx['gen_ai.request.model'] == null) { ctx['gen_ai.request.model'] = mn; } "
                        "def rm = ctx['response_model'] ?: ctx['actual_model']; "
                        "if (rm != null && ctx['gen_ai.response.model'] == null) { ctx['gen_ai.response.model'] = rm; } "
                        "def rid = ctx['response_id'] ?: ctx['completion_id']; "
                        "if (rid != null && ctx['gen_ai.response.id'] == null) { ctx['gen_ai.response.id'] = rid; } "
                        "def fr = ctx['finish_reason'] ?: ctx['finish_reasons']; "
                        "if (fr != null && ctx['gen_ai.response.finish_reasons'] == null) { ctx['gen_ai.response.finish_reasons'] = fr; } "
                        "def otpe = ctx['output_type'] ?: ctx['response_type']; "
                        "if (otpe != null && ctx['gen_ai.output.type'] == null) { ctx['gen_ai.output.type'] = otpe; } "
                        # session
                        "def sid = ctx['session_id'] ?: ctx['conversation_id'] ?: ctx['thread_id']; "
                        "if (sid != null && ctx['gen_ai.conversation.id'] == null) { ctx['gen_ai.conversation.id'] = sid; } "
                        # agent
                        "def aid = ctx['agent_id'] ?: ctx['run_id']; if (aid != null && ctx['gen_ai.agent.id'] == null) { ctx['gen_ai.agent.id'] = aid; } "
                        "def anm = ctx['agent_name']; if (anm != null && ctx['gen_ai.agent.name'] == null) { ctx['gen_ai.agent.name'] = anm; } "
                        "def ads = ctx['agent_description']; if (ads != null && ctx['gen_ai.agent.description'] == null) { ctx['gen_ai.agent.description'] = ads; } "
                        # tokens
                        "def it = ctx['input_tokens'] ?: ctx['prompt_tokens']; "
                        "if (it != null && ctx['gen_ai.usage.input_tokens'] == null) { ctx['gen_ai.usage.input_tokens'] = it; } "
                        "def ot = ctx['output_tokens'] ?: ctx['completion_tokens']; "
                        "if (ot != null && ctx['gen_ai.usage.output_tokens'] == null) { ctx['gen_ai.usage.output_tokens'] = ot; } "
                        "def tt = ctx['total_tokens']; if (tt != null && ctx['gen_ai.usage.total_tokens'] == null) { ctx['gen_ai.usage.total_tokens'] = tt; } "
                        "def crt = ctx['cache_read_input_tokens'] ?: ctx['cached_input_tokens']; "
                        "if (crt != null && ctx['gen_ai.usage.cache_read.input_tokens'] == null) { ctx['gen_ai.usage.cache_read.input_tokens'] = crt; } "
                        "def cct = ctx['cache_creation_input_tokens']; "
                        "if (cct != null && ctx['gen_ai.usage.cache_creation.input_tokens'] == null) { ctx['gen_ai.usage.cache_creation.input_tokens'] = cct; } "
                        # retrieval / MCP
                        "def dsid = ctx['data_source_id'] ?: ctx['knowledge_source']; "
                        "if (dsid != null && ctx['gen_ai.data_source.id'] == null) { ctx['gen_ai.data_source.id'] = dsid; } "
                        "def mpn = ctx['prompt_name']; if (mpn != null && ctx['gen_ai.prompt.name'] == null) { ctx['gen_ai.prompt.name'] = mpn; } "
                        "def mmn = ctx['mcp_method'] ?: ctx['mcp_method_name']; "
                        "if (mmn != null && ctx['mcp.method.name'] == null) { ctx['mcp.method.name'] = mmn; } "
                        "def msid = ctx['mcp_session_id']; if (msid != null && ctx['mcp.session.id'] == null) { ctx['mcp.session.id'] = msid; } "
                        "def muri = ctx['mcp_resource_uri'] ?: ctx['resource_uri']; "
                        "if (muri != null && ctx['mcp.resource.uri'] == null) { ctx['mcp.resource.uri'] = muri; } "
                        # --- 2. latency_ms → event.duration (ms → nanoseconds) ---
                        "if (ctx['gen_ai.agent_ext.latency_ms'] != null && ctx.event?.duration == null) { "
                        "  ctx.event = ctx.event ?: new HashMap(); "
                        "  ctx.event.duration = (long)(ctx['gen_ai.agent_ext.latency_ms'] * 1000000L); "
                        "} "
                        # --- 3. event.outcome derivation (unified) ---
                        "if (ctx.event?.outcome == null) { "
                        "  ctx.event = ctx.event ?: new HashMap(); "
                        # Try status string first
                        "  def st = ctx['status']; "
                        "  if (st instanceof String) { "
                        "    String sl = st.toLowerCase(); "
                        "    if (sl.equals('success') || sl.equals('ok') || sl.equals('pass')) { ctx.event.outcome = 'success'; } "
                        "    else if (sl.equals('failure') || sl.equals('fail') || sl.equals('error')) { ctx.event.outcome = 'failure'; } "
                        "  } "
                        # Try boolean success
                        "  if (ctx.event.outcome == null) { "
                        "    def sc = ctx['success']; "
                        "    if (sc instanceof Boolean) { ctx.event.outcome = sc ? 'success' : 'failure'; } "
                        "  } "
                        # Fallback: error.type presence
                        "  if (ctx.event.outcome == null) { "
                        "    ctx.event.outcome = (ctx.error?.type != null) ? 'failure' : 'success'; "
                        "  } "
                        "}"
                    ),
                    "ignore_failure": True,
                }
            },
            # --- redact sensitive GenAI payloads + PII governance (single script) ---
            {"remove": {"field": "gen_ai.prompt", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.completion", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.input.messages", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.output.messages", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.system_instructions", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.tool.definitions", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.tool.call.arguments", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.tool.call.result", "ignore_missing": True}},
            {"remove": {"field": "prompt", "ignore_missing": True}},
            {"remove": {"field": "completion", "ignore_missing": True}},
            {"remove": {"field": "messages", "ignore_missing": True}},
            {"remove": {"field": "system_prompt", "ignore_missing": True}},
            {"remove": {"field": "tool_args", "ignore_missing": True}},
            {"remove": {"field": "tool_result", "ignore_missing": True}},
            {
                "script": {
                    "lang": "painless",
                    "source": (
                        # Remove flat dotted sensitive keys
                        "def sensitive = ['gen_ai.prompt', 'gen_ai.completion', "
                        "'gen_ai.tool.call.arguments', 'gen_ai.tool.call.result']; "
                        "for (String f : sensitive) { ctx.remove(f); } "
                        # Truncate reasoning trace fields to prevent PII leakage
                        "int MAX_RATIONALE = 500; "
                        "int MAX_INPUT_SUMMARY = 300; "
                        "def r = ctx['gen_ai.agent_ext.reasoning.rationale']; "
                        "if (r instanceof String && r.length() > MAX_RATIONALE) { "
                        "  ctx['gen_ai.agent_ext.reasoning.rationale'] = r.substring(0, MAX_RATIONALE) + '... [truncated]'; "
                        "} "
                        "def s = ctx['gen_ai.agent_ext.reasoning.input_summary']; "
                        "if (s instanceof String && s.length() > MAX_INPUT_SUMMARY) { "
                        "  ctx['gen_ai.agent_ext.reasoning.input_summary'] = s.substring(0, MAX_INPUT_SUMMARY) + '... [truncated]'; "
                        "} "
                        "def c = ctx['gen_ai.feedback.comment']; "
                        "if (c instanceof String && c.length() > 1000) { "
                        "  ctx['gen_ai.feedback.comment'] = c.substring(0, 1000) + '... [truncated]'; "
                        "} "
                        "def msg = ctx.message; "
                        "if (msg instanceof String) { "
                        "  String ml = msg.toLowerCase(); "
                        "  if ((ml.contains('gen_ai.prompt') || ml.contains('gen_ai.completion') || "
                        "       ml.contains('gen_ai.input.messages') || ml.contains('gen_ai.output.messages') || "
                        "       ml.contains('gen_ai.tool.call.arguments') || ml.contains('gen_ai.tool.call.result')) && ml.length() > 80) { "
                        "    ctx.message = '[redacted sensitive GenAI payload]'; "
                        "    ctx.labels = ctx.labels ?: new HashMap(); "
                        "    ctx.labels.payload_truncated = true; "
                        "  } "
                        "}"
                    ),
                    "ignore_failure": True,
                }
            },
        ],
        "on_failure": [
            {"set": {"field": "observer.ingest_error", "value": "{{ _ingest.on_failure_message }}"}}
        ],
    }


# ---------------------------------------------------------------------------
# ILM — tiered lifecycle
# ---------------------------------------------------------------------------

def build_ilm_policy(retention_days: int) -> dict[str, Any]:
    warm_age = max(1, retention_days // 5)
    cold_age = max(warm_age + 1, retention_days // 2)
    return {
        "policy": {
            "_meta": {
                "product": "elasticsearch-agent-observability",
                "managed": True,
                "retention_days": retention_days,
            },
            "phases": {
                "hot": {
                    "actions": {
                        "rollover": {
                            "max_age": "7d",
                            "max_primary_shard_size": "25gb",
                            "max_docs": 50_000_000,
                        }
                    }
                },
                "warm": {
                    "min_age": f"{warm_age}d",
                    "actions": {
                        "shrink": {"number_of_shards": 1},
                        "forcemerge": {"max_num_segments": 1},
                        "readonly": {},
                    },
                },
                "cold": {
                    "min_age": f"{cold_age}d",
                    "actions": {
                        "readonly": {},
                    },
                },
                "delete": {
                    "min_age": f"{retention_days}d",
                    "actions": {"delete": {}},
                },
            }
        }
    }


# ---------------------------------------------------------------------------
# Kibana saved objects — Lens visualizations, searches, and dashboard
# ---------------------------------------------------------------------------

def _search_source(data_view_id: str, query: str = "") -> dict[str, Any]:
    return {
        "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
        "query": {"language": "kuery", "query": query},
        "filter": [],
    }


def build_search_saved_object(*, object_id: str, title: str, description: str, data_view_id: str, columns: list[str] | None = None, query: str = "") -> dict[str, Any]:
    return {
        "type": "search",
        "id": object_id,
        "attributes": {
            "title": title,
            "description": description,
            "columns": columns or DEFAULT_KIBANA_COLUMNS,
            "sort": [["@timestamp", "desc"]],
            "grid": {},
            "hideChart": False,
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(_search_source(data_view_id, query), separators=(",", ":")),
            },
        },
        "references": [
            {
                "id": data_view_id,
                "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                "type": "index-pattern",
            }
        ],
    }


DEFAULT_LENS_LAYER_ID = "layer1"
_LENS_CURRENT_REF = "indexpattern-datasource-current-indexpattern"
_LENS_LAYER_REF_PREFIX = "indexpattern-datasource-layer-"

# Kibana 9.x renamed the Lens datasource from "indexpattern" to "formBased".
# Using "formBased" here so panels render on 9.x. Kibana 8.14+ also accepts
# "formBased" (it was introduced as an alias in 8.x before becoming the only
# name in 9.x). If you need to support Kibana < 8.14, change this back to
# "indexpattern".
_LENS_DATASOURCE_KEY = "formBased"


def _build_lens_state(*, columns: dict[str, Any], column_order: list[str], visualization: dict[str, Any], layer_id: str = DEFAULT_LENS_LAYER_ID, query: str = "") -> dict[str, Any]:
    return {
        "adHocDataViews": {},
        "datasourceStates": {
            _LENS_DATASOURCE_KEY: {
                "currentIndexPatternId": _LENS_CURRENT_REF,
                "layers": {
                    layer_id: {
                        "columns": columns,
                        "columnOrder": column_order,
                        "incompleteColumns": {},
                        "indexPatternId": f"{_LENS_LAYER_REF_PREFIX}{layer_id}",
                    }
                },
            }
        },
        "filters": [],
        "internalReferences": [],
        "query": {"language": "kuery", "query": query},
        "visualization": visualization,
    }


def build_lens_saved_object(*, object_id: str, title: str, description: str, visualization_type: str, state: dict[str, Any], data_view_id: str) -> dict[str, Any]:
    return {
        "type": "lens",
        "id": object_id,
        "attributes": {
            "title": title,
            "description": description,
            "visualizationType": visualization_type,
            "state": state,
        },
        "references": [
            {"id": data_view_id, "type": "index-pattern", "name": _LENS_CURRENT_REF},
            {"id": data_view_id, "type": "index-pattern", "name": f"{_LENS_LAYER_REF_PREFIX}{DEFAULT_LENS_LAYER_ID}"},
        ],
    }


def _build_terms_pie_visualization(
    *,
    object_id: str,
    data_view_id: str,
    title: str,
    description: str,
    source_field: str,
    metric_label: str,
    size: int = 10,
    query: str = "",
) -> dict[str, Any]:
    state = _build_lens_state(
        columns={
            "col-slice": {"operationType": "terms", "sourceField": source_field, "params": {"size": size}},
            "col-metric": {"operationType": "count", "label": metric_label},
        },
        column_order=["col-slice", "col-metric"],
        visualization={
            "shape": "pie",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "primaryGroups": ["col-slice"], "metric": "col-metric"}],
        },
        query=query,
    )
    return build_lens_saved_object(
        object_id=object_id,
        title=title,
        description=description,
        visualization_type="lnsPie",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_metric_visualization(
    *,
    object_id: str,
    data_view_id: str,
    title: str,
    description: str,
    operation_type: str = "count",
    source_field: str | None = None,
    label: str = "",
    query: str = "",
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single big-number KPI metric panel (lnsMetric)."""
    col: dict[str, Any] = {"operationType": operation_type, "label": label or title}
    if source_field:
        col["sourceField"] = source_field
    if extra_params:
        col["params"] = extra_params
    state = _build_lens_state(
        columns={"col-metric": col},
        column_order=["col-metric"],
        visualization={
            "layerId": DEFAULT_LENS_LAYER_ID,
            "accessor": "col-metric",
            "layerType": "data",
        },
        query=query,
    )
    return build_lens_saved_object(
        object_id=object_id,
        title=title,
        description=description,
        visualization_type="lnsMetric",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_table_visualization(
    *,
    object_id: str,
    data_view_id: str,
    title: str,
    description: str,
    source_field: str,
    metric_label: str = "Count",
    size: int = 10,
    query: str = "",
    sort_direction: str = "desc",
) -> dict[str, Any]:
    """Top-N table panel (lnsDatatable)."""
    state = _build_lens_state(
        columns={
            "col-bucket": {
                "operationType": "terms",
                "sourceField": source_field,
                "params": {"size": size, "orderDirection": sort_direction, "orderBy": {"type": "column", "columnId": "col-metric"}},
            },
            "col-metric": {"operationType": "count", "label": metric_label},
        },
        column_order=["col-bucket", "col-metric"],
        visualization={
            "layerId": DEFAULT_LENS_LAYER_ID,
            "layerType": "data",
            "columns": [
                {"columnId": "col-bucket", "isTransposed": False},
                {"columnId": "col-metric", "isTransposed": False},
            ],
        },
        query=query,
    )
    return build_lens_saved_object(
        object_id=object_id,
        title=title,
        description=description,
        visualization_type="lnsDatatable",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_terms_avg_table(
    *,
    object_id: str,
    data_view_id: str,
    title: str,
    description: str,
    bucket_field: str,
    metric_field: str,
    metric_label: str,
    size: int = 10,
    query: str = "",
) -> dict[str, Any]:
    """Top-N table with terms bucket and average metric."""
    state = _build_lens_state(
        columns={
            "col-bucket": {
                "operationType": "terms",
                "sourceField": bucket_field,
                "params": {"size": size, "orderDirection": "desc", "orderBy": {"type": "column", "columnId": "col-metric"}},
            },
            "col-metric": {"operationType": "average", "sourceField": metric_field, "label": metric_label, "customLabel": True},
        },
        column_order=["col-bucket", "col-metric"],
        visualization={
            "layerId": DEFAULT_LENS_LAYER_ID,
            "layerType": "data",
            "columns": [
                {"columnId": "col-bucket", "isTransposed": False},
                {"columnId": "col-metric", "isTransposed": False},
            ],
        },
        query=query,
    )
    return build_lens_saved_object(
        object_id=object_id,
        title=title,
        description=description,
        visualization_type="lnsDatatable",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_horizontal_bar(
    *,
    object_id: str,
    data_view_id: str,
    title: str,
    description: str,
    source_field: str,
    metric_label: str = "Count",
    size: int = 10,
    query: str = "",
) -> dict[str, Any]:
    """Horizontal bar chart — terms on Y axis, count on X axis."""
    state = _build_lens_state(
        columns={
            "col-y": {"operationType": "terms", "sourceField": source_field, "params": {"size": size}},
            "col-x": {"operationType": "count", "label": metric_label},
        },
        column_order=["col-y", "col-x"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "bar_horizontal",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-x"], "splitAccessor": "col-y"}],
        },
        query=query,
    )
    return build_lens_saved_object(
        object_id=object_id,
        title=title,
        description=description,
        visualization_type="lnsXY",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_event_rate_visualization(*, object_id: str, data_view_id: str) -> dict[str, Any]:
    """Lens XY chart: event count over time, broken down by event.outcome."""
    state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-y": {"operationType": "count", "label": "Events"},
            "col-breakdown": {"operationType": "terms", "sourceField": "event.outcome", "params": {"size": 5}},
        },
        column_order=["col-x", "col-breakdown", "col-y"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "bar_stacked",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-y"], "splitAccessor": "col-breakdown"}],
        },
    )
    return build_lens_saved_object(
        object_id=object_id,
        title="Event Rate by Outcome",
        description="Event volume over time, split by success/failure.",
        visualization_type="lnsXY",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_latency_percentiles(*, object_id: str, data_view_id: str) -> dict[str, Any]:
    """Lens XY chart: P50 and P95 latency over time."""
    state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-p50": {"operationType": "percentile", "sourceField": "gen_ai.agent_ext.latency_ms", "params": {"percentile": 50}, "label": "P50 latency (ms)", "customLabel": True},
            "col-p95": {"operationType": "percentile", "sourceField": "gen_ai.agent_ext.latency_ms", "params": {"percentile": 95}, "label": "P95 latency (ms)", "customLabel": True},
        },
        column_order=["col-x", "col-p50", "col-p95"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "line",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-p50", "col-p95"]}],
        },
    )
    return build_lens_saved_object(
        object_id=object_id,
        title="Latency Trend (P50 / P95)",
        description="P50 and P95 latency in milliseconds over time.",
        visualization_type="lnsXY",
        state=state,
        data_view_id=data_view_id,
    )


def _build_lens_token_usage(*, object_id: str, data_view_id: str) -> dict[str, Any]:
    """Lens XY: token usage over time (input vs output)."""
    state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-input": {"operationType": "sum", "sourceField": "gen_ai.usage.input_tokens", "label": "Input tokens"},
            "col-output": {"operationType": "sum", "sourceField": "gen_ai.usage.output_tokens", "label": "Output tokens"},
        },
        column_order=["col-x", "col-input", "col-output"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "area_stacked",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-input", "col-output"]}],
        },
    )
    return build_lens_saved_object(
        object_id=object_id,
        title="Token Usage over Time",
        description="Input vs output token consumption per time bucket.",
        visualization_type="lnsXY",
        state=state,
        data_view_id=data_view_id,
    )


def build_dashboard_saved_object(*, object_id: str, title: str, description: str, panel_refs: list[dict[str, str]]) -> dict[str, Any]:
    """Build a Kibana dashboard saved object with auto-flow grid layout.

    Kibana uses a 48-column grid. Panels are placed left-to-right; when a panel
    doesn't fit in the remaining space on the current row, it wraps to the next row.
    """
    panels = []
    references = []
    # Kibana grid is 48 columns wide
    GRID_WIDTH = 48
    cur_x = 0
    cur_y = 0
    row_height = 0
    for index, ref in enumerate(panel_refs):
        ref_name = f"panel_{index}"
        panel_type = ref.get("type", "search")
        width = int(ref.get("width", "24"))
        height = int(ref.get("height", "15"))
        # Wrap to next row if panel doesn't fit
        if cur_x + width > GRID_WIDTH:
            cur_y += row_height
            cur_x = 0
            row_height = 0
        panels.append(
            {
                "version": "9.0.0",
                "type": panel_type,
                "panelIndex": str(index + 1),
                "gridData": {"x": cur_x, "y": cur_y, "w": width, "h": height, "i": str(index + 1)},
                "panelRefName": ref_name,
                "embeddableConfig": {},
            }
        )
        references.append({"type": panel_type, "name": ref_name, "id": ref["id"]})
        cur_x += width
        row_height = max(row_height, height)
    return {
        "type": "dashboard",
        "id": object_id,
        "attributes": {
            "title": title,
            "description": description,
            "panelsJSON": json.dumps(panels, separators=(",", ":")),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": True, "syncCursor": True, "syncTooltips": True}, separators=(",", ":")),
            "timeRestore": True,
            "timeTo": "now",
            "timeFrom": "now-24h",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({"query": {"language": "kuery", "query": ""}, "filter": []}, separators=(",", ":")),
            },
        },
        "references": references,
    }


def build_kibana_saved_objects(index_prefix: str, *, extensions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ds_name = build_data_stream_name(index_prefix)
    data_view_id = f"{index_prefix}-events-view"
    saved_search_id = f"{index_prefix}-event-stream"
    failure_search_id = f"{index_prefix}-event-failures"
    session_search_id = f"{index_prefix}-session-drilldown"
    trace_timeline_id = f"{index_prefix}-trace-timeline"
    mcp_search_id = f"{index_prefix}-mcp-tool-calls"
    dashboard_id = f"{index_prefix}-overview"

    # --- IDs for all lens panels ---
    # KPI metrics (row 1)
    kpi_event_count_id = f"{index_prefix}-kpi-event-count"
    kpi_token_input_id = f"{index_prefix}-kpi-token-input"
    kpi_token_output_id = f"{index_prefix}-kpi-token-output"
    kpi_avg_latency_id = f"{index_prefix}-kpi-avg-latency"
    # Core charts
    lens_event_rate_id = f"{index_prefix}-lens-event-rate"
    lens_latency_id = f"{index_prefix}-lens-latency"
    lens_token_usage_id = f"{index_prefix}-lens-token-usage"
    lens_operation_types_id = f"{index_prefix}-lens-operation-types"
    lens_model_dist_id = f"{index_prefix}-lens-model-distribution"
    # Session & tools
    lens_top_sessions_id = f"{index_prefix}-lens-top-sessions"
    lens_failed_sessions_id = f"{index_prefix}-lens-failed-sessions"
    lens_top_tools_id = f"{index_prefix}-lens-top-tools"
    lens_component_type_id = f"{index_prefix}-lens-component-type"
    lens_component_failures_id = f"{index_prefix}-lens-component-failures"
    # Error & retry
    lens_error_types_id = f"{index_prefix}-lens-error-types"
    lens_error_trend_id = f"{index_prefix}-lens-error-trend"
    lens_retry_storm_id = f"{index_prefix}-lens-retry-storm"
    # Session replay
    lens_trace_timeline_table_id = f"{index_prefix}-lens-trace-timeline-table"
    lens_turn_latency_id = f"{index_prefix}-lens-turn-latency"
    # Guardrail
    lens_guardrail_actions_id = f"{index_prefix}-lens-guardrail-actions"
    lens_guardrail_categories_id = f"{index_prefix}-lens-guardrail-categories"
    # Evaluation
    lens_eval_outcomes_id = f"{index_prefix}-lens-eval-outcomes"
    lens_eval_dimensions_id = f"{index_prefix}-lens-eval-dimensions"
    # Reasoning
    lens_reasoning_actions_id = f"{index_prefix}-lens-reasoning-actions"
    lens_reasoning_decision_types_id = f"{index_prefix}-lens-reasoning-decision-types"
    # User feedback
    lens_feedback_sentiment_id = f"{index_prefix}-lens-feedback-sentiment"
    lens_feedback_score_id = f"{index_prefix}-lens-feedback-score"

    objects: list[dict[str, Any]] = [
        # --- Data view ---
        {
            "type": "index-pattern",
            "id": data_view_id,
            "attributes": {
                "title": f"{ds_name}*",
                "name": "Agent observability events",
                "timeFieldName": "@timestamp",
            },
        },
        # --- Saved searches ---
        build_search_saved_object(
            object_id=saved_search_id,
            title="Agent observability event stream",
            description="Default Kibana Discover surface for agent observability events.",
            data_view_id=data_view_id,
            columns=DEFAULT_KIBANA_COLUMNS,
        ),
        build_search_saved_object(
            object_id=failure_search_id,
            title="Agent observability failures",
            description="Search focused on failure and ingest-error events.",
            data_view_id=data_view_id,
            columns=DEFAULT_KIBANA_COLUMNS,
            query="event.outcome:failure or observer.ingest_error:*",
        ),
        build_search_saved_object(
            object_id=session_search_id,
            title="Agent session drilldown",
            description="Conversation-first Discover entry with session, run, turn, and component context.",
            data_view_id=data_view_id,
            columns=DEFAULT_KIBANA_COLUMNS,
            query="gen_ai.conversation.id:* or gen_ai.agent_ext.turn_id:* or gen_ai.agent.id:*",
        ),
        build_search_saved_object(
            object_id=trace_timeline_id,
            title="Trace timeline",
            description="Step-by-step replay of a single trace. Filter by trace.id to see the full execution sequence.",
            data_view_id=data_view_id,
            columns=[
                "@timestamp", "event.action", "event.outcome",
                "gen_ai.tool.name", "gen_ai.request.model",
                "gen_ai.agent_ext.component_type", "gen_ai.agent_ext.latency_ms",
                "gen_ai.agent_ext.turn_id", "span.id",
                "gen_ai.agent_ext.parent_agent.id",
                "gen_ai.agent_ext.reasoning.action",
                "gen_ai.agent_ext.reasoning.decision_type",
                "gen_ai.agent_ext.reasoning.rationale",
            ],
            query="trace.id:*",
        ),
        build_search_saved_object(
            object_id=mcp_search_id,
            title="MCP tool calls",
            description="MCP-first Discover entry. Filter by method, tool, session, and outcome.",
            data_view_id=data_view_id,
            columns=[
                "@timestamp", "mcp.method.name", "gen_ai.tool.name", "mcp.session.id",
                "gen_ai.conversation.id", "gen_ai.agent_ext.latency_ms", "event.outcome",
                "error.type", "trace.id", "span.id",
            ],
            query="mcp.method.name:* or gen_ai.agent_ext.component_type:mcp",
        ),
        # =====================================================================
        # KPI metric panels (row 1: 4 small cards)
        # =====================================================================
        _build_lens_metric_visualization(
            object_id=kpi_event_count_id, data_view_id=data_view_id,
            title="Total Events", description="Total events in selected time range.",
            operation_type="count", label="Count",
        ),
        _build_lens_metric_visualization(
            object_id=kpi_token_input_id, data_view_id=data_view_id,
            title="Input Tokens", description="Sum of input tokens consumed.",
            operation_type="sum", source_field="gen_ai.usage.input_tokens", label="Input",
        ),
        _build_lens_metric_visualization(
            object_id=kpi_token_output_id, data_view_id=data_view_id,
            title="Output Tokens", description="Sum of output tokens generated.",
            operation_type="sum", source_field="gen_ai.usage.output_tokens", label="Output",
        ),
        _build_lens_metric_visualization(
            object_id=kpi_avg_latency_id, data_view_id=data_view_id,
            title="Avg Latency (ms)", description="Average latency in milliseconds.",
            operation_type="average", source_field="gen_ai.agent_ext.latency_ms", label="Avg Latency (ms)",
        ),
        # =====================================================================
        # Core time-series charts (row 2-3)
        # =====================================================================
        _build_lens_event_rate_visualization(object_id=lens_event_rate_id, data_view_id=data_view_id),
        _build_lens_latency_percentiles(object_id=lens_latency_id, data_view_id=data_view_id),
        _build_lens_token_usage(object_id=lens_token_usage_id, data_view_id=data_view_id),
        # Operation types — horizontal bar
        _build_lens_horizontal_bar(
            object_id=lens_operation_types_id, data_view_id=data_view_id,
            title="Operations",
            description="Event count by operation type.",
            source_field="gen_ai.operation.name", metric_label="Count",
        ),
        # Model distribution — donut
        _build_terms_pie_visualization(
            object_id=lens_model_dist_id, data_view_id=data_view_id,
            title="Model Distribution",
            description="Event count by model.",
            source_field="gen_ai.request.model", metric_label="Events",
        ),
        # =====================================================================
        # Session & tool panels (tables + pie)
        # =====================================================================
        _build_lens_table_visualization(
            object_id=lens_top_sessions_id, data_view_id=data_view_id,
            title="Top Sessions by Volume",
            description="Most active sessions ranked by event count.",
            source_field="gen_ai.conversation.id", metric_label="Count",
        ),
        _build_lens_table_visualization(
            object_id=lens_failed_sessions_id, data_view_id=data_view_id,
            title="Failed Sessions",
            description="Sessions with most failures for fast drilldown.",
            source_field="gen_ai.conversation.id", metric_label="Count",
            query="event.outcome:failure and gen_ai.conversation.id:*",
        ),
        _build_lens_horizontal_bar(
            object_id=lens_top_tools_id, data_view_id=data_view_id,
            title="Top Tools",
            description="Most-called agent tools.",
            source_field="gen_ai.tool.name", metric_label="Count",
        ),
        _build_terms_pie_visualization(
            object_id=lens_component_type_id, data_view_id=data_view_id,
            title="Component Types",
            description="Event breakdown by component type (runtime / tool / llm / mcp).",
            source_field="gen_ai.agent_ext.component_type", metric_label="Events",
        ),
        _build_lens_horizontal_bar(
            object_id=lens_component_failures_id, data_view_id=data_view_id,
            title="Failures by Component",
            description="Which component types produce the most failures.",
            source_field="gen_ai.agent_ext.component_type", metric_label="Count",
            query="event.outcome:failure and gen_ai.agent_ext.component_type:*",
        ),
        # =====================================================================
        # Error & Retry panels
        # =====================================================================
        _build_terms_pie_visualization(
            object_id=lens_error_types_id, data_view_id=data_view_id,
            title="Error Types",
            description="Breakdown of error types.",
            source_field="error.type", metric_label="Errors",
            query="error.type:*",
        ),
    ]

    # Error trend over time
    error_trend_state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-y": {"operationType": "count", "label": "Errors"},
            "col-break": {"operationType": "terms", "sourceField": "error.type", "params": {"size": 5}},
        },
        column_order=["col-x", "col-break", "col-y"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "bar_stacked",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-y"], "splitAccessor": "col-break"}],
        },
        query="error.type:*",
    )
    objects.append(build_lens_saved_object(
        object_id=lens_error_trend_id, title="Error Trend",
        description="Error volume over time by type.",
        visualization_type="lnsXY", state=error_trend_state, data_view_id=data_view_id,
    ))

    # Retry storm — table of sessions with high retry counts
    objects.append(_build_lens_table_visualization(
        object_id=lens_retry_storm_id, data_view_id=data_view_id,
        title="Retry Storm",
        description="Sessions with excessive retries.",
        source_field="gen_ai.conversation.id", metric_label="Total Retries",
        query="gen_ai.agent_ext.retry_count > 0",
    ))

    # =====================================================================
    # Session replay helpers
    # =====================================================================
    objects.append(_build_lens_table_visualization(
        object_id=lens_trace_timeline_table_id, data_view_id=data_view_id,
        title="Trace Steps",
        description="Events per trace for step-by-step replay.",
        source_field="trace.id", metric_label="Count",
    ))
    objects.append(_build_lens_terms_avg_table(
        object_id=lens_turn_latency_id, data_view_id=data_view_id,
        title="Turn Latency",
        description="Average latency by turn.",
        bucket_field="gen_ai.agent_ext.turn_id",
        metric_field="gen_ai.agent_ext.latency_ms",
        metric_label="Avg Latency (ms)",
        query="gen_ai.agent_ext.turn_id:* and gen_ai.agent_ext.latency_ms:*",
    ))

    # =====================================================================
    # Guardrail panels
    # =====================================================================
    objects.append(_build_terms_pie_visualization(
        object_id=lens_guardrail_actions_id, data_view_id=data_view_id,
        title="Guardrail Actions", description="Distribution of guardrail decisions: pass / block / redact.",
        source_field="gen_ai.guardrail.action", metric_label="Events",
        query="gen_ai.guardrail.action:*",
    ))
    objects.append(_build_terms_pie_visualization(
        object_id=lens_guardrail_categories_id, data_view_id=data_view_id,
        title="Guardrail Categories", description="Safety categories: content_safety, prompt_injection, pii, custom.",
        source_field="gen_ai.guardrail.category", metric_label="Events",
        query="gen_ai.guardrail.category:*",
    ))

    # =====================================================================
    # Evaluation panels
    # =====================================================================
    objects.append(_build_terms_pie_visualization(
        object_id=lens_eval_outcomes_id, data_view_id=data_view_id,
        title="Evaluation Outcomes", description="Pass / fail / degraded distribution.",
        source_field="gen_ai.evaluation.outcome", metric_label="Evaluations",
        query="gen_ai.evaluation.outcome:*",
    ))
    objects.append(_build_terms_pie_visualization(
        object_id=lens_eval_dimensions_id, data_view_id=data_view_id,
        title="Evaluation Dimensions", description="Breakdown by dimension: quality, safety, latency.",
        source_field="gen_ai.evaluation.dimension", metric_label="Evaluations",
        query="gen_ai.evaluation.dimension:*",
    ))

    # =====================================================================
    # Reasoning trace panels
    # =====================================================================
    objects.append(_build_terms_pie_visualization(
        object_id=lens_reasoning_actions_id, data_view_id=data_view_id,
        title="Reasoning Actions", description="Agent decision actions: tool_call / delegate / respond / wait / escalate.",
        source_field="gen_ai.agent_ext.reasoning.action", metric_label="Decisions",
        query="gen_ai.agent_ext.reasoning.action:*",
    ))
    objects.append(_build_terms_pie_visualization(
        object_id=lens_reasoning_decision_types_id, data_view_id=data_view_id,
        title="Reasoning Decision Types", description="Decision types: routing, tool_selection, delegation, termination, retry.",
        source_field="gen_ai.agent_ext.reasoning.decision_type", metric_label="Decisions",
        query="gen_ai.agent_ext.reasoning.decision_type:*",
    ))

    # =====================================================================
    # User feedback panels
    # =====================================================================
    objects.append(_build_terms_pie_visualization(
        object_id=lens_feedback_sentiment_id, data_view_id=data_view_id,
        title="Feedback Sentiment", description="Positive / negative / neutral distribution.",
        source_field="gen_ai.feedback.sentiment", metric_label="Feedback",
        query="gen_ai.feedback.sentiment:*",
    ))
    feedback_score_state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-avg": {"operationType": "average", "sourceField": "gen_ai.feedback.score", "label": "Avg Feedback Score", "customLabel": True},
        },
        column_order=["col-x", "col-avg"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "line",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-avg"]}],
        },
    )
    objects.append(build_lens_saved_object(
        object_id=lens_feedback_score_id, title="Feedback Score over Time",
        description="Average user feedback score over time.",
        visualization_type="lnsXY", state=feedback_score_state, data_view_id=data_view_id,
    ))

    # =====================================================================
    # Dashboard layout — panels ordered by importance
    # =====================================================================
    # Row 1: KPI cards (4 × 12w × 6h)
    # Row 2-3: Core charts (event rate, latency, token, operation types, model dist)
    # Row 4-5: Session & tools
    # Row 6: Error & retry
    # Row 7: Session replay helpers
    # Row 8: Guardrail/Eval/Reasoning/Feedback (advanced, often empty)
    # Row 9: Discover saved searches
    dashboard_panels = [
        # --- KPI row (4 cards, each 12 wide, 6 tall) ---
        {"id": kpi_event_count_id, "type": "lens", "width": "12", "height": "6"},
        {"id": kpi_token_input_id, "type": "lens", "width": "12", "height": "6"},
        {"id": kpi_token_output_id, "type": "lens", "width": "12", "height": "6"},
        {"id": kpi_avg_latency_id, "type": "lens", "width": "12", "height": "6"},
        # --- Core charts ---
        {"id": lens_event_rate_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_latency_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_token_usage_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_operation_types_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_model_dist_id, "type": "lens", "width": "16", "height": "12"},
        # --- Session & tools ---
        {"id": lens_top_sessions_id, "type": "lens", "width": "24", "height": "10"},
        {"id": lens_failed_sessions_id, "type": "lens", "width": "24", "height": "10"},
        {"id": lens_top_tools_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_component_type_id, "type": "lens", "width": "16", "height": "12"},
        {"id": lens_component_failures_id, "type": "lens", "width": "16", "height": "12"},
        # --- Error & retry ---
        {"id": lens_error_types_id, "type": "lens", "width": "16", "height": "12"},
        {"id": lens_error_trend_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_retry_storm_id, "type": "lens", "width": "24", "height": "10"},
        # --- Session replay ---
        {"id": lens_trace_timeline_table_id, "type": "lens", "width": "24", "height": "10"},
        {"id": lens_turn_latency_id, "type": "lens", "width": "16", "height": "12"},
        # --- Guardrail ---
        {"id": lens_guardrail_actions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_guardrail_categories_id, "type": "lens", "width": "24", "height": "12"},
        # --- Evaluation ---
        {"id": lens_eval_outcomes_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_eval_dimensions_id, "type": "lens", "width": "24", "height": "12"},
        # --- Reasoning ---
        {"id": lens_reasoning_actions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_reasoning_decision_types_id, "type": "lens", "width": "24", "height": "12"},
        # --- Feedback ---
        {"id": lens_feedback_sentiment_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_feedback_score_id, "type": "lens", "width": "24", "height": "12"},
        # --- Discover tables (full width, one per row) ---
        {"id": saved_search_id, "type": "search", "width": "48", "height": "15"},
        {"id": failure_search_id, "type": "search", "width": "48", "height": "15"},
        {"id": session_search_id, "type": "search", "width": "48", "height": "15"},
        {"id": trace_timeline_id, "type": "search", "width": "48", "height": "15"},
    ]

    extra_lens_ids: list[str] = []
    for ext in (extensions or []):
        ext_id = f"{index_prefix}-lens-{ext.get('id', 'custom')}"
        source_field = ext.get("field", "gen_ai.tool.name")
        agg_type = ext.get("aggregation", "terms")
        viz_type = ext.get("visualization", "lnsPie")
        title = ext.get("title", f"Custom: {source_field}")
        size = ext.get("size", 10)

        if agg_type == "terms":
            columns = {
                "col-slice": {"operationType": "terms", "sourceField": source_field, "params": {"size": size}},
                "col-metric": {"operationType": "count", "label": "Count"},
            }
            viz_config = {"shape": "pie", "layers": [{"layerId": "layer1", "primaryGroups": ["col-slice"], "metric": "col-metric"}]}
        elif agg_type == "sum":
            columns = {
                "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
                "col-y": {"operationType": "sum", "sourceField": source_field, "label": f"Sum of {source_field}"},
            }
            viz_type = "lnsXY"
            viz_config = {"preferredSeriesType": "bar", "layers": [{"layerId": "layer1", "xAccessor": "col-x", "accessors": ["col-y"]}]}
        elif agg_type == "percentile":
            columns = {
                "col-metric": {"operationType": "percentile", "sourceField": source_field, "params": {"percentile": ext.get("percentile", 95)}, "label": f"P{ext.get('percentile', 95)}"},
            }
            viz_type = "lnsMetric"
            viz_config = {"layerId": "layer1", "accessor": "col-metric"}
        else:
            continue

        lens_obj = build_lens_saved_object(
            object_id=ext_id,
            title=title,
            description=ext.get("description", f"Custom panel for {source_field}"),
            visualization_type=viz_type,
            state=_build_lens_state(
                columns=columns,
                column_order=list(columns.keys()),
                visualization=viz_config,
            ),
            data_view_id=data_view_id,
        )
        objects.append(lens_obj)
        dashboard_panels.append({"id": ext_id, "type": "lens", "width": str(ext.get("width", 24)), "height": str(ext.get("height", 12))})
        extra_lens_ids.append(ext_id)

    objects.append(
        build_dashboard_saved_object(
            object_id=dashboard_id,
            title="Agent observability overview",
            description="Dashboard with session-first drilldown, component hotspots, event rate, latency, tool distribution, token usage, event stream, and failure stream.",
            panel_refs=dashboard_panels,
        ),
    )

    return {
        "space": "default",
        "objects": objects,
        "summary": {
            "data_view_id": data_view_id,
            "saved_search_id": saved_search_id,
            "failure_search_id": failure_search_id,
            "session_search_id": session_search_id,
            "trace_timeline_id": trace_timeline_id,
            "mcp_search_id": mcp_search_id,
            "dashboard_id": dashboard_id,
            "lens_ids": [
                kpi_event_count_id,
                kpi_token_input_id,
                kpi_token_output_id,
                kpi_avg_latency_id,
                lens_event_rate_id,
                lens_latency_id,
                lens_token_usage_id,
                lens_operation_types_id,
                lens_model_dist_id,
                lens_top_sessions_id,
                lens_failed_sessions_id,
                lens_top_tools_id,
                lens_component_type_id,
                lens_component_failures_id,
                lens_error_types_id,
                lens_error_trend_id,
                lens_retry_storm_id,
                lens_trace_timeline_table_id,
                lens_turn_latency_id,
                lens_guardrail_actions_id,
                lens_guardrail_categories_id,
                lens_eval_outcomes_id,
                lens_eval_dimensions_id,
                lens_reasoning_actions_id,
                lens_reasoning_decision_types_id,
                lens_feedback_sentiment_id,
                lens_feedback_score_id,
            ] + extra_lens_ids,
            "events_alias_pattern": f"{ds_name}*",
            "object_count": len(objects),
        },
    }


# ---------------------------------------------------------------------------
# Elastic-native investigation packs
# ---------------------------------------------------------------------------

def build_investigation_queries(index_prefix: str) -> dict[str, Any]:
    ds_name = build_data_stream_name(index_prefix)
    return {
        "product": "elasticsearch-agent-observability",
        "type": "esql-investigation-pack",
        "data_stream": ds_name,
        "queries": [
            {
                "id": "slow-answers",
                "title": "Slow Answers",
                "when": "P95 latency or single sessions are slow.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 24 hours AND gen_ai.agent_ext.latency_ms IS NOT NULL | STATS avg_latency=AVG(gen_ai.agent_ext.latency_ms), p95_latency=PERCENTILE(gen_ai.agent_ext.latency_ms, 95), events=COUNT(*) BY gen_ai.conversation.id | SORT p95_latency DESC | LIMIT 20",
            },
            {
                "id": "failed-sessions",
                "title": "Failed Sessions",
                "when": "A user reports a broken or incomplete agent run.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 24 hours AND event.outcome == \"failure\" | STATS failures=COUNT(*), errors=VALUES(error.type), tools=VALUES(gen_ai.tool.name) BY gen_ai.conversation.id | SORT failures DESC | LIMIT 20",
            },
            {
                "id": "tool-error-hotspots",
                "title": "Tool Error Hotspots",
                "when": "Tool calls fail or retry too often.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 24 hours AND gen_ai.tool.name IS NOT NULL | EVAL failed = CASE(event.outcome == \"failure\", 1, 0) | STATS calls=COUNT(*), failures=SUM(failed) BY gen_ai.tool.name, error.type | EVAL failure_rate = failures / calls | SORT failures DESC | LIMIT 20",
            },
            {
                "id": "token-spikes",
                "title": "Token Spikes",
                "when": "Token usage jumps after a prompt, model, or routing change.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 24 hours | STATS input_tokens=SUM(gen_ai.usage.input_tokens), output_tokens=SUM(gen_ai.usage.output_tokens), events=COUNT(*) BY gen_ai.request.model, gen_ai.conversation.id | EVAL total_tokens = input_tokens + output_tokens | SORT total_tokens DESC | LIMIT 20",
            },
            {
                "id": "mcp-tool-calls",
                "title": "MCP Tool Calls",
                "when": "MCP server/tool latency or errors need drilldown.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 24 hours AND (mcp.method.name IS NOT NULL OR gen_ai.agent_ext.component_type == \"mcp\") | EVAL failed = CASE(event.outcome == \"failure\", 1, 0) | STATS events=COUNT(*), failures=SUM(failed), avg_latency=AVG(gen_ai.agent_ext.latency_ms) BY mcp.method.name, gen_ai.tool.name, mcp.session.id | SORT failures DESC, avg_latency DESC | LIMIT 20",
            },
            {
                "id": "low-feedback",
                "title": "Low Feedback",
                "when": "Users give negative feedback and you need the linked traces.",
                "language": "esql",
                "query": f"FROM {ds_name}* | WHERE @timestamp >= NOW() - 7 days AND gen_ai.feedback.score IS NOT NULL | WHERE gen_ai.feedback.score < 0 OR gen_ai.feedback.sentiment == \"negative\" | KEEP @timestamp, trace.id, gen_ai.conversation.id, gen_ai.feedback.score, gen_ai.feedback.sentiment, gen_ai.feedback.comment | SORT @timestamp DESC | LIMIT 50",
            },
        ],
    }


def build_alert_rule_specs(index_prefix: str) -> dict[str, Any]:
    ds_name = build_data_stream_name(index_prefix)
    return {
        "product": "elasticsearch-agent-observability",
        "type": "kibana-query-rule-specs",
        "note": "Reference payloads for Kibana Elasticsearch query rules. Apply via Kibana Alerting UI/API; no ES/Kibana source changes required.",
        "rules": [
            {
                "id": "agent-error-spike",
                "title": "Agent error spike",
                "query_language": "kuery",
                "index": f"{ds_name}*",
                "time_field": "@timestamp",
                "query": "event.outcome:failure and not event.dataset:internal.*",
                "window": "5m",
                "threshold": {"metric": "count", "operator": "above", "value": 10, "group_by": ["service.name", "gen_ai.agent_ext.component_type"]},
            },
            {
                "id": "slow-agent-turns",
                "title": "Slow agent turns",
                "query_language": "kuery",
                "index": f"{ds_name}*",
                "time_field": "@timestamp",
                "query": "gen_ai.agent_ext.latency_ms > 30000 and gen_ai.conversation.id:*",
                "window": "10m",
                "threshold": {"metric": "count", "operator": "above", "value": 3, "group_by": ["gen_ai.conversation.id"]},
            },
            {
                "id": "mcp-tool-failures",
                "title": "MCP tool failures",
                "query_language": "kuery",
                "index": f"{ds_name}*",
                "time_field": "@timestamp",
                "query": "event.outcome:failure and (mcp.method.name:* or gen_ai.agent_ext.component_type:mcp)",
                "window": "10m",
                "threshold": {"metric": "count", "operator": "above", "value": 5, "group_by": ["mcp.method.name", "gen_ai.tool.name"]},
            },
            {
                "id": "negative-feedback",
                "title": "Negative user feedback",
                "query_language": "kuery",
                "index": f"{ds_name}*",
                "time_field": "@timestamp",
                "query": "gen_ai.feedback.sentiment:negative or gen_ai.feedback.score < 0",
                "window": "15m",
                "threshold": {"metric": "count", "operator": "above", "value": 0, "group_by": ["service.name"]},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Report config
# ---------------------------------------------------------------------------

def build_report_config(index_prefix: str, discovery: dict[str, Any], *, extensions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    modules = sorted({module["module_kind"] for module in discovery.get("detected_modules", []) if module.get("module_kind")})
    kibana_bundle = build_kibana_saved_objects(index_prefix, extensions=extensions)
    return {
        "time_range": "now-24h",
        "time_field": "@timestamp",
        "index_prefix": index_prefix,
        "events_alias": build_events_alias(index_prefix),
        "data_stream": build_data_stream_name(index_prefix),
        "recommended_modules": modules,
        "human_surface": "kibana_dashboard",
        "kibana": kibana_bundle["summary"],
        "investigations": [query["id"] for query in build_investigation_queries(index_prefix)["queries"]],
        "alert_rule_specs": [rule["id"] for rule in build_alert_rule_specs(index_prefix)["rules"]],
        "metrics": [
            "success_rate",
            "p50_latency_ms",
            "p95_latency_ms",
            "tool_error_rate",
            "retry_total",
            "token_input_total",
            "token_output_total",
            "top_sessions",
            "failed_sessions",
            "slow_turns",
            "top_components",
            "failed_components",
            "top_tools",
            "top_models",
            "mcp_methods",
            "error_types",
        ],
    }


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_assets(discovery: dict[str, Any], output_dir: Path, *, index_prefix: str, retention_days: int, extensions: list[dict[str, Any]] | None = None) -> dict[str, str]:
    ensure_dir(output_dir)
    validated_prefix = validate_index_prefix(index_prefix)
    validated_retention_days = validate_positive_int(retention_days, "Retention days")
    modules = sorted({module["module_kind"] for module in discovery.get("detected_modules", []) if module.get("module_kind")})

    component_ecs_base = build_component_template_ecs_base(validated_prefix)
    component_settings = build_component_template_settings(validated_prefix, validated_retention_days)
    index_template = build_index_template(validated_prefix, modules)
    ingest_pipeline = build_ingest_pipeline(modules)
    ilm_policy = build_ilm_policy(validated_retention_days)
    kibana_saved_objects = build_kibana_saved_objects(validated_prefix, extensions=extensions)
    investigation_queries = build_investigation_queries(validated_prefix)
    alert_rule_specs = build_alert_rule_specs(validated_prefix)
    report_config = build_report_config(validated_prefix, discovery, extensions=extensions)

    paths: dict[str, Path] = {
        "component_template_ecs_base": output_dir / "component-template-ecs-base.json",
        "component_template_settings": output_dir / "component-template-settings.json",
        "index_template": output_dir / "index-template.json",
        "ingest_pipeline": output_dir / "ingest-pipeline.json",
        "ilm_policy": output_dir / "ilm-policy.json",
        "report_config": output_dir / "report-config.json",
        "investigation_queries": output_dir / "investigation-queries.json",
        "alert_rule_specs": output_dir / "alert-rule-specs.json",
        "kibana_saved_objects_json": output_dir / "kibana-saved-objects.json",
        "kibana_saved_objects_ndjson": output_dir / "kibana-saved-objects.ndjson",
    }
    write_json(paths["component_template_ecs_base"], component_ecs_base)
    write_json(paths["component_template_settings"], component_settings)
    write_json(paths["index_template"], index_template)
    write_json(paths["ingest_pipeline"], ingest_pipeline)
    write_json(paths["ilm_policy"], ilm_policy)
    write_json(paths["report_config"], report_config)
    write_json(paths["investigation_queries"], investigation_queries)
    write_json(paths["alert_rule_specs"], alert_rule_specs)
    write_json(paths["kibana_saved_objects_json"], kibana_saved_objects)
    write_text(
        paths["kibana_saved_objects_ndjson"],
        "\n".join(json.dumps(item, ensure_ascii=False) for item in kibana_saved_objects["objects"]) + "\n",
    )
    return {key: str(path) for key, path in paths.items()}


def main() -> int:
    try:
        args = parse_args()
        discovery = read_json(Path(args.discovery).expanduser().resolve())
        output_dir = Path(args.output_dir).expanduser().resolve()
        extensions = None
        if args.dashboard_extensions:
            ext_path = Path(args.dashboard_extensions).expanduser().resolve()
            ext_data = read_json(ext_path)
            if isinstance(ext_data, list):
                extensions = ext_data
            elif isinstance(ext_data, dict) and "panels" in ext_data:
                extensions = ext_data["panels"]
            else:
                raise SkillError("Dashboard extensions file must be a JSON array or an object with a 'panels' key")
        paths = render_assets(discovery, output_dir, index_prefix=args.index_prefix, retention_days=args.retention_days, extensions=extensions)
        print(f"✅ Elasticsearch assets written to: {output_dir}")
        for name, path in paths.items():
            print(f"   {name}: {path}")
        return 0
    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to render Elasticsearch assets: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
