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
        "gen_ai.request.model": {"type": "keyword"},
        "gen_ai.response.model": {"type": "keyword"},
        "gen_ai.operation.name": {"type": "keyword"},
        "gen_ai.usage.input_tokens": {"type": "long"},
        "gen_ai.usage.output_tokens": {"type": "long"},
        "gen_ai.usage.total_tokens": {"type": "long"},
        # --- gen_ai OTel standard: agent + tool + conversation ---
        "gen_ai.agent.id": {"type": "keyword"},
        "gen_ai.agent.name": {"type": "keyword"},
        "gen_ai.agent.version": {"type": "keyword"},
        "gen_ai.conversation.id": {"type": "keyword"},
        "gen_ai.tool.name": {"type": "keyword"},
        "gen_ai.tool.call.id": {"type": "keyword"},
        "error.type": {"type": "keyword"},
        # --- agent_ext: project extensions awaiting OTel SemConv proposal ---
        "gen_ai.agent_ext.turn_id": {"type": "keyword"},
        "gen_ai.agent_ext.component_type": {"type": "keyword"},  # runtime / tool / llm / mcp / memory / knowledge / guardrail
        "gen_ai.agent_ext.retry_count": {"type": "integer"},
        "gen_ai.agent_ext.latency_ms": {"type": "float"},
        "gen_ai.agent_ext.cost": {"type": "double"},
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
        "gen_ai.evaluation.dimension": {"type": "keyword"},  # quality / safety / latency / cost
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
                        "'labels', 'gen_ai', 'error', 'log']); "
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
                        # model
                        "def mn = ctx['model'] ?: ctx['model_name']; "
                        "if (mn != null && ctx['gen_ai.request.model'] == null) { ctx['gen_ai.request.model'] = mn; } "
                        # session
                        "def sid = ctx['session_id'] ?: ctx['conversation_id']; "
                        "if (sid != null && ctx['gen_ai.conversation.id'] == null) { ctx['gen_ai.conversation.id'] = sid; } "
                        # agent
                        "def aid = ctx['agent_id']; if (aid != null && ctx['gen_ai.agent.id'] == null) { ctx['gen_ai.agent.id'] = aid; } "
                        "def anm = ctx['agent_name']; if (anm != null && ctx['gen_ai.agent.name'] == null) { ctx['gen_ai.agent.name'] = anm; } "
                        # tokens
                        "def it = ctx['input_tokens'] ?: ctx['prompt_tokens']; "
                        "if (it != null && ctx['gen_ai.usage.input_tokens'] == null) { ctx['gen_ai.usage.input_tokens'] = it; } "
                        "def ot = ctx['output_tokens'] ?: ctx['completion_tokens']; "
                        "if (ot != null && ctx['gen_ai.usage.output_tokens'] == null) { ctx['gen_ai.usage.output_tokens'] = ot; } "
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
            {"remove": {"field": "gen_ai.tool.call.arguments", "ignore_missing": True}},
            {"remove": {"field": "gen_ai.tool.call.result", "ignore_missing": True}},
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
        title="Agent event rate",
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
            "col-p50": {"operationType": "percentile", "sourceField": "event.duration", "params": {"percentile": 50}, "label": "P50 duration (ns → divide by 1e6 for ms)"},
            "col-p95": {"operationType": "percentile", "sourceField": "event.duration", "params": {"percentile": 95}, "label": "P95 duration (ns → divide by 1e6 for ms)"},
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
        title="Agent latency over time (P50 / P95)",
        description="P50 and P95 event.duration over time.",
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
        title="Token usage over time",
        description="Input vs output token consumption per time bucket.",
        visualization_type="lnsXY",
        state=state,
        data_view_id=data_view_id,
    )


def build_dashboard_saved_object(*, object_id: str, title: str, description: str, panel_refs: list[dict[str, str]]) -> dict[str, Any]:
    panels = []
    references = []
    row = 0
    for index, ref in enumerate(panel_refs):
        ref_name = f"panel_{index}"
        panel_type = ref.get("type", "search")
        width = int(ref.get("width", "24"))
        height = int(ref.get("height", "15"))
        panels.append(
            {
                "version": "9.0.0",
                "type": panel_type,
                "panelIndex": str(index + 1),
                "gridData": {"x": (index % 2) * 24, "y": row, "w": width, "h": height, "i": str(index + 1)},
                "panelRefName": ref_name,
                "embeddableConfig": {},
            }
        )
        references.append({"type": panel_type, "name": ref_name, "id": ref["id"]})
        if index % 2 == 1:
            row += height
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
    dashboard_id = f"{index_prefix}-overview"
    lens_event_rate_id = f"{index_prefix}-lens-event-rate"
    lens_latency_id = f"{index_prefix}-lens-latency"
    lens_top_sessions_id = f"{index_prefix}-lens-top-sessions"
    lens_failed_sessions_id = f"{index_prefix}-lens-failed-sessions"
    lens_top_tools_id = f"{index_prefix}-lens-top-tools"
    lens_token_usage_id = f"{index_prefix}-lens-token-usage"
    lens_component_type_id = f"{index_prefix}-lens-component-type"
    lens_component_failures_id = f"{index_prefix}-lens-component-failures"
    # New: Guardrail, Evaluation, Cost panels
    lens_guardrail_actions_id = f"{index_prefix}-lens-guardrail-actions"
    lens_guardrail_categories_id = f"{index_prefix}-lens-guardrail-categories"
    lens_eval_outcomes_id = f"{index_prefix}-lens-eval-outcomes"
    lens_eval_dimensions_id = f"{index_prefix}-lens-eval-dimensions"
    lens_cost_by_model_id = f"{index_prefix}-lens-cost-by-model"
    lens_cost_over_time_id = f"{index_prefix}-lens-cost-over-time"
    # Reasoning trace panels
    lens_reasoning_actions_id = f"{index_prefix}-lens-reasoning-actions"
    lens_reasoning_decision_types_id = f"{index_prefix}-lens-reasoning-decision-types"
    # User feedback panels
    lens_feedback_sentiment_id = f"{index_prefix}-lens-feedback-sentiment"
    lens_feedback_score_id = f"{index_prefix}-lens-feedback-score"

    objects: list[dict[str, Any]] = [
        {
            "type": "index-pattern",
            "id": data_view_id,
            "attributes": {
                "title": f"{ds_name}*",
                "name": "Agent observability events",
                "timeFieldName": "@timestamp",
            },
        },
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
        _build_lens_event_rate_visualization(object_id=lens_event_rate_id, data_view_id=data_view_id),
        _build_lens_latency_percentiles(object_id=lens_latency_id, data_view_id=data_view_id),
        _build_terms_pie_visualization(
            object_id=lens_top_sessions_id, data_view_id=data_view_id,
            title="Top sessions by event volume",
            description="Most active gen_ai.conversation.id values in the selected time window.",
            source_field="gen_ai.conversation.id", metric_label="Events",
        ),
        _build_terms_pie_visualization(
            object_id=lens_failed_sessions_id, data_view_id=data_view_id,
            title="Failed sessions",
            description="Failure-heavy sessions for fast conversation-level drilldown.",
            source_field="gen_ai.conversation.id", metric_label="Failures",
            query="event.outcome: failure and gen_ai.conversation.id:*",
        ),
        _build_terms_pie_visualization(
            object_id=lens_top_tools_id, data_view_id=data_view_id,
            title="Top tools by call count",
            description="Pie chart of most-called agent tools.",
            source_field="gen_ai.tool.name", metric_label="Calls",
        ),
        _build_lens_token_usage(object_id=lens_token_usage_id, data_view_id=data_view_id),
        _build_terms_pie_visualization(
            object_id=lens_component_type_id,
            data_view_id=data_view_id,
            title="Events by component type",
            description="Breakdown by gen_ai.agent_ext.component_type (runtime / tool / llm / mcp / memory / knowledge / guardrail).",
            source_field="gen_ai.agent_ext.component_type",
            metric_label="Events",
        ),
        _build_terms_pie_visualization(
            object_id=lens_component_failures_id, data_view_id=data_view_id,
            title="Failure hotspots by component",
            description="Which component types are producing the most failed events.",
            source_field="gen_ai.agent_ext.component_type", metric_label="Failures",
            query="event.outcome: failure and gen_ai.agent_ext.component_type:*",
        ),
        # --- Guardrail panels ---
        _build_terms_pie_visualization(
            object_id=lens_guardrail_actions_id, data_view_id=data_view_id,
            title="Guardrail actions",
            description="Distribution of guardrail decisions: pass / block / redact.",
            source_field="gen_ai.guardrail.action", metric_label="Events",
            query="gen_ai.guardrail.action:*",
        ),
        _build_terms_pie_visualization(
            object_id=lens_guardrail_categories_id, data_view_id=data_view_id,
            title="Guardrail categories",
            description="Which safety categories are firing: content_safety, prompt_injection, pii, custom.",
            source_field="gen_ai.guardrail.category", metric_label="Events",
            query="gen_ai.guardrail.category:*",
        ),
        # --- Evaluation panels ---
        _build_terms_pie_visualization(
            object_id=lens_eval_outcomes_id, data_view_id=data_view_id,
            title="Evaluation outcomes",
            description="Pass / fail / degraded distribution from evaluation runs.",
            source_field="gen_ai.evaluation.outcome", metric_label="Evaluations",
            query="gen_ai.evaluation.outcome:*",
        ),
        _build_terms_pie_visualization(
            object_id=lens_eval_dimensions_id, data_view_id=data_view_id,
            title="Evaluation dimensions",
            description="Breakdown by evaluation dimension: quality, safety, latency, cost.",
            source_field="gen_ai.evaluation.dimension", metric_label="Evaluations",
            query="gen_ai.evaluation.dimension:*",
        ),
        # --- Cost panels ---
        _build_terms_pie_visualization(
            object_id=lens_cost_by_model_id, data_view_id=data_view_id,
            title="Cost by model",
            description="USD cost distribution by model. Requires gen_ai.agent_ext.cost to be populated.",
            source_field="gen_ai.request.model", metric_label="Cost",
            query="gen_ai.agent_ext.cost:*",
        ),
    ]

    # Cost over time XY chart
    cost_time_state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-cost": {"operationType": "sum", "sourceField": "gen_ai.agent_ext.cost", "label": "Cost (USD)"},
        },
        column_order=["col-x", "col-cost"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "bar",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-cost"]}],
        },
    )
    objects.append(
        build_lens_saved_object(
            object_id=lens_cost_over_time_id,
            title="Cost over time",
            description="Total USD cost per time bucket. Requires gen_ai.agent_ext.cost.",
            visualization_type="lnsXY",
            state=cost_time_state,
            data_view_id=data_view_id,
        ),
    )

    # Reasoning trace panels
    objects.append(
        _build_terms_pie_visualization(
            object_id=lens_reasoning_actions_id, data_view_id=data_view_id,
            title="Reasoning actions",
            description="Distribution of agent decision actions: tool_call / delegate / respond / wait / escalate.",
            source_field="gen_ai.agent_ext.reasoning.action", metric_label="Decisions",
            query="gen_ai.agent_ext.reasoning.action:*",
        ),
    )
    objects.append(
        _build_terms_pie_visualization(
            object_id=lens_reasoning_decision_types_id, data_view_id=data_view_id,
            title="Decision types",
            description="Breakdown of agent reasoning decision types: routing, tool_selection, delegation, termination, retry.",
            source_field="gen_ai.agent_ext.reasoning.decision_type", metric_label="Decisions",
            query="gen_ai.agent_ext.reasoning.decision_type:*",
        ),
    )

    # User feedback panels
    objects.append(
        _build_terms_pie_visualization(
            object_id=lens_feedback_sentiment_id, data_view_id=data_view_id,
            title="User feedback sentiment",
            description="Distribution of user feedback: positive / negative / neutral.",
            source_field="gen_ai.feedback.sentiment", metric_label="Feedback",
            query="gen_ai.feedback.sentiment:*",
        ),
    )
    feedback_score_state = _build_lens_state(
        columns={
            "col-x": {"operationType": "date_histogram", "sourceField": "@timestamp", "params": {"interval": "auto"}},
            "col-avg": {"operationType": "average", "sourceField": "gen_ai.feedback.score", "label": "Avg feedback score"},
        },
        column_order=["col-x", "col-avg"],
        visualization={
            "legend": {"isVisible": True, "position": "right"},
            "preferredSeriesType": "line",
            "layers": [{"layerId": DEFAULT_LENS_LAYER_ID, "xAccessor": "col-x", "accessors": ["col-avg"]}],
        },
    )
    objects.append(
        build_lens_saved_object(
            object_id=lens_feedback_score_id,
            title="Feedback score over time",
            description="Average user feedback score over time. Track quality trends.",
            visualization_type="lnsXY",
            state=feedback_score_state,
            data_view_id=data_view_id,
        ),
    )

    dashboard_panels = [
        {"id": lens_event_rate_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_latency_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_top_sessions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_failed_sessions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_component_type_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_component_failures_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_top_tools_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_token_usage_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_cost_by_model_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_cost_over_time_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_guardrail_actions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_guardrail_categories_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_eval_outcomes_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_eval_dimensions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_reasoning_actions_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_reasoning_decision_types_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_feedback_sentiment_id, "type": "lens", "width": "24", "height": "12"},
        {"id": lens_feedback_score_id, "type": "lens", "width": "24", "height": "12"},
        {"id": session_search_id, "type": "search", "width": "24", "height": "15"},
        {"id": trace_timeline_id, "type": "search", "width": "24", "height": "15"},
        {"id": saved_search_id, "type": "search", "width": "24", "height": "15"},
        {"id": failure_search_id, "type": "search", "width": "24", "height": "15"},
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
            "dashboard_id": dashboard_id,
            "lens_ids": [
                lens_event_rate_id,
                lens_latency_id,
                lens_top_sessions_id,
                lens_failed_sessions_id,
                lens_top_tools_id,
                lens_token_usage_id,
                lens_component_type_id,
                lens_component_failures_id,
                lens_guardrail_actions_id,
                lens_guardrail_categories_id,
                lens_eval_outcomes_id,
                lens_eval_dimensions_id,
                lens_cost_by_model_id,
                lens_cost_over_time_id,
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
        "metrics": [
            "success_rate",
            "p50_latency_ms",
            "p95_latency_ms",
            "tool_error_rate",
            "retry_total",
            "token_input_total",
            "token_output_total",
            "cost_total",
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
    report_config = build_report_config(validated_prefix, discovery, extensions=extensions)

    paths: dict[str, Path] = {
        "component_template_ecs_base": output_dir / "component-template-ecs-base.json",
        "component_template_settings": output_dir / "component-template-settings.json",
        "index_template": output_dir / "index-template.json",
        "ingest_pipeline": output_dir / "ingest-pipeline.json",
        "ilm_policy": output_dir / "ilm-policy.json",
        "report_config": output_dir / "report-config.json",
        "kibana_saved_objects_json": output_dir / "kibana-saved-objects.json",
        "kibana_saved_objects_ndjson": output_dir / "kibana-saved-objects.ndjson",
    }
    write_json(paths["component_template_ecs_base"], component_ecs_base)
    write_json(paths["component_template_settings"], component_settings)
    write_json(paths["index_template"], index_template)
    write_json(paths["ingest_pipeline"], ingest_pipeline)
    write_json(paths["ilm_policy"], ilm_policy)
    write_json(paths["report_config"], report_config)
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
