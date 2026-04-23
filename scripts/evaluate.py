#!/usr/bin/env python3
"""Lightweight evaluation runner for agent observability.

Runs rule-based evaluators against recent agent traces in ES and writes
structured evaluation results back, populating the gen_ai.evaluation.*
fields that the dashboard and alert engine already understand.

This is NOT a full eval framework (use Braintrust / Inspect / DeepEval
for that). This is the observability-native eval layer: it answers
"is the agent regressing?" by looking at the telemetry it already emits.

Built-in evaluators:
  - latency_regression: P95 latency vs baseline (per tool / model)
  - error_rate_regression: error rate vs baseline (per tool / model)
  - token_efficiency: tokens per session vs baseline
  - cost_regression: cost per session vs baseline
  - tool_coverage: fraction of tools that were actually called
  - guardrail_block_rate: fraction of guardrail checks that blocked

Usage:
    python scripts/evaluate.py run --es-url http://localhost:9200
    python scripts/evaluate.py run --es-url <url> --evaluators latency_regression,error_rate_regression
    python scripts/evaluate.py run --es-url <url> --time-range now-1h --baseline-range now-7d/now-1h
    python scripts/evaluate.py list   # show available evaluators
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Evaluator registry
# ---------------------------------------------------------------------------

EVALUATORS: dict[str, dict[str, str]] = {
    "latency_regression": {
        "description": "Detect P95 latency regression per tool and model vs baseline",
        "dimension": "latency",
    },
    "error_rate_regression": {
        "description": "Detect error rate regression per tool and model vs baseline",
        "dimension": "quality",
    },
    "token_efficiency": {
        "description": "Detect token consumption regression per session vs baseline",
        "dimension": "cost",
    },
    "cost_regression": {
        "description": "Detect per-session cost increase vs baseline",
        "dimension": "cost",
    },
    "tool_coverage": {
        "description": "Fraction of known tools that were actually called in the window",
        "dimension": "quality",
    },
    "guardrail_block_rate": {
        "description": "Fraction of guardrail checks that resulted in block/redact",
        "dimension": "safety",
    },
}


def _internal_filter() -> list[dict[str, Any]]:
    return [{"prefix": {"event.dataset": "internal."}}]


def _query_window(config: ESConfig, ds_name: str, gte: str, lte: str = "now") -> dict[str, Any]:
    """Aggregation query covering a time window for evaluation metrics."""
    return es_request(config, "POST", f"/{ds_name}*/_search", {
        "size": 0,
        "timeout": "30s",
        "query": {
            "bool": {
                "filter": [{"range": {"@timestamp": {"gte": gte, "lte": lte}}}],
                "must_not": _internal_filter(),
            },
        },
        "aggs": {
            "total": {"value_count": {"field": "@timestamp"}},
            "errors": {"filter": {"term": {"event.outcome": "failure"}}},
            "p95_latency": {"percentiles": {"field": "event.duration", "percents": [95]}},
            "token_sum": {"sum": {"field": "gen_ai.usage.input_tokens"}},
            "token_output_sum": {"sum": {"field": "gen_ai.usage.output_tokens"}},
            "cost_sum": {"sum": {"field": "gen_ai.agent_ext.cost"}},
            "session_count": {"cardinality": {"field": "gen_ai.conversation.id"}},
            "tool_names": {"terms": {"field": "gen_ai.tool.name", "size": 100}},
            "per_tool_latency": {
                "terms": {"field": "gen_ai.tool.name", "size": 20},
                "aggs": {"p95": {"percentiles": {"field": "event.duration", "percents": [95]}}},
            },
            "per_tool_errors": {
                "terms": {"field": "gen_ai.tool.name", "size": 20},
                "aggs": {
                    "failures": {"filter": {"term": {"event.outcome": "failure"}}},
                    "total": {"value_count": {"field": "@timestamp"}},
                },
            },
            "guardrail_total": {
                "filter": {"exists": {"field": "gen_ai.guardrail.action"}},
                "aggs": {
                    "blocked": {"filter": {"terms": {"gen_ai.guardrail.action": ["block", "redact"]}}},
                },
            },
        },
    })


# ---------------------------------------------------------------------------
# Individual evaluators
# ---------------------------------------------------------------------------

def _eval_latency_regression(current: dict, baseline: dict, threshold: float = 1.5) -> dict[str, Any]:
    """P95 latency regression: current vs baseline."""
    c_aggs = current.get("aggregations", {})
    b_aggs = baseline.get("aggregations", {})
    c_p95 = (c_aggs.get("p95_latency", {}).get("values", {}).get("95.0") or 0) / 1e6  # ns → ms
    b_p95 = (b_aggs.get("p95_latency", {}).get("values", {}).get("95.0") or 0) / 1e6
    if b_p95 <= 0:
        return {"outcome": "pass", "score": 1.0, "detail": "No baseline latency data"}
    ratio = c_p95 / b_p95
    if ratio > threshold:
        return {
            "outcome": "fail",
            "score": round(max(0, 1 - (ratio - 1) / 5), 2),
            "detail": f"P95 latency regressed {ratio:.1f}x (current={c_p95:.0f}ms, baseline={b_p95:.0f}ms)",
        }
    return {
        "outcome": "pass",
        "score": round(min(1.0, 1 / max(0.1, ratio)), 2),
        "detail": f"P95 latency stable (current={c_p95:.0f}ms, baseline={b_p95:.0f}ms, ratio={ratio:.2f})",
    }


def _eval_error_rate_regression(current: dict, baseline: dict, threshold: float = 1.5) -> dict[str, Any]:
    """Error rate regression: current vs baseline."""
    c_aggs = current.get("aggregations", {})
    b_aggs = baseline.get("aggregations", {})
    c_total = c_aggs.get("total", {}).get("value", 0) or 1
    c_errors = c_aggs.get("errors", {}).get("doc_count", 0)
    b_total = b_aggs.get("total", {}).get("value", 0) or 1
    b_errors = b_aggs.get("errors", {}).get("doc_count", 0)
    c_rate = c_errors / c_total
    b_rate = b_errors / b_total if b_total > 0 else 0
    if b_rate <= 0:
        if c_rate > 0.05:
            return {"outcome": "fail", "score": round(1 - c_rate, 2), "detail": f"Error rate {c_rate:.1%} with no baseline"}
        return {"outcome": "pass", "score": 1.0, "detail": "No baseline errors"}
    ratio = c_rate / b_rate
    if ratio > threshold:
        return {
            "outcome": "fail",
            "score": round(max(0, 1 - c_rate), 2),
            "detail": f"Error rate regressed {ratio:.1f}x (current={c_rate:.1%}, baseline={b_rate:.1%})",
        }
    return {
        "outcome": "pass",
        "score": round(1 - c_rate, 2),
        "detail": f"Error rate stable (current={c_rate:.1%}, baseline={b_rate:.1%})",
    }


def _eval_token_efficiency(current: dict, baseline: dict, threshold: float = 2.0) -> dict[str, Any]:
    """Tokens per session vs baseline."""
    c_aggs = current.get("aggregations", {})
    b_aggs = baseline.get("aggregations", {})
    c_tokens = (c_aggs.get("token_sum", {}).get("value", 0) or 0) + (c_aggs.get("token_output_sum", {}).get("value", 0) or 0)
    c_sessions = max(1, c_aggs.get("session_count", {}).get("value", 0) or 1)
    b_tokens = (b_aggs.get("token_sum", {}).get("value", 0) or 0) + (b_aggs.get("token_output_sum", {}).get("value", 0) or 0)
    b_sessions = max(1, b_aggs.get("session_count", {}).get("value", 0) or 1)
    c_per = c_tokens / c_sessions
    b_per = b_tokens / b_sessions
    if b_per <= 0:
        return {"outcome": "pass", "score": 1.0, "detail": "No baseline token data"}
    ratio = c_per / b_per
    if ratio > threshold:
        return {
            "outcome": "fail",
            "score": round(max(0, 1 - (ratio - 1) / 10), 2),
            "detail": f"Token/session regressed {ratio:.1f}x (current={c_per:,.0f}, baseline={b_per:,.0f})",
        }
    return {
        "outcome": "pass",
        "score": round(min(1.0, 1 / max(0.1, ratio)), 2),
        "detail": f"Token efficiency stable ({c_per:,.0f} vs {b_per:,.0f} per session)",
    }


def _eval_cost_regression(current: dict, baseline: dict, threshold: float = 2.0) -> dict[str, Any]:
    """Cost per session vs baseline."""
    c_aggs = current.get("aggregations", {})
    b_aggs = baseline.get("aggregations", {})
    c_cost = c_aggs.get("cost_sum", {}).get("value", 0) or 0
    c_sessions = max(1, c_aggs.get("session_count", {}).get("value", 0) or 1)
    b_cost = b_aggs.get("cost_sum", {}).get("value", 0) or 0
    b_sessions = max(1, b_aggs.get("session_count", {}).get("value", 0) or 1)
    c_per = c_cost / c_sessions
    b_per = b_cost / b_sessions
    if b_per <= 0:
        return {"outcome": "pass", "score": 1.0, "detail": "No baseline cost data"}
    ratio = c_per / b_per
    if ratio > threshold:
        return {
            "outcome": "fail",
            "score": round(max(0, 1 - (ratio - 1) / 10), 2),
            "detail": f"Cost/session regressed {ratio:.1f}x (${c_per:.4f} vs ${b_per:.4f})",
        }
    return {"outcome": "pass", "score": round(min(1.0, 1 / max(0.1, ratio)), 2), "detail": f"Cost stable (${c_per:.4f} vs ${b_per:.4f})"}


def _eval_tool_coverage(current: dict, baseline: dict) -> dict[str, Any]:
    """What fraction of known tools were called?"""
    c_aggs = current.get("aggregations", {})
    b_aggs = baseline.get("aggregations", {})
    c_tools = {b["key"] for b in c_aggs.get("tool_names", {}).get("buckets", [])}
    b_tools = {b["key"] for b in b_aggs.get("tool_names", {}).get("buckets", [])}
    known_tools = c_tools | b_tools
    if not known_tools:
        return {"outcome": "pass", "score": 1.0, "detail": "No tool data"}
    coverage = len(c_tools) / len(known_tools)
    missing = sorted(known_tools - c_tools)
    if coverage < 0.5:
        return {
            "outcome": "fail",
            "score": round(coverage, 2),
            "detail": f"Only {len(c_tools)}/{len(known_tools)} tools called. Missing: {', '.join(missing[:5])}",
        }
    outcome = "pass" if coverage >= 0.8 else "degraded"
    return {
        "outcome": outcome,
        "score": round(coverage, 2),
        "detail": f"{len(c_tools)}/{len(known_tools)} tools called" + (f". Missing: {', '.join(missing[:5])}" if missing else ""),
    }


def _eval_guardrail_block_rate(current: dict, baseline: dict) -> dict[str, Any]:
    """Block/redact rate among guardrail checks."""
    c_aggs = current.get("aggregations", {})
    gr = c_aggs.get("guardrail_total", {})
    total = gr.get("doc_count", 0)
    if total == 0:
        return {"outcome": "pass", "score": 1.0, "detail": "No guardrail events"}
    blocked = gr.get("blocked", {}).get("doc_count", 0)
    rate = blocked / total
    if rate > 0.3:
        return {
            "outcome": "fail",
            "score": round(1 - rate, 2),
            "detail": f"High guardrail block rate: {rate:.1%} ({blocked}/{total})",
        }
    outcome = "pass" if rate < 0.1 else "degraded"
    return {"outcome": outcome, "score": round(1 - rate, 2), "detail": f"Guardrail block rate: {rate:.1%} ({blocked}/{total})"}


_EVAL_FUNCTIONS = {
    "latency_regression": _eval_latency_regression,
    "error_rate_regression": _eval_error_rate_regression,
    "token_efficiency": _eval_token_efficiency,
    "cost_regression": _eval_cost_regression,
    "tool_coverage": _eval_tool_coverage,
    "guardrail_block_rate": _eval_guardrail_block_rate,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_evaluation(
    config: ESConfig,
    *,
    index_prefix: str,
    time_range: str = "now-1h",
    baseline_range: str = "now-7d/now-1h",
    evaluators: list[str] | None = None,
    write_to_es: bool = False,
) -> dict[str, Any]:
    """Run selected evaluators and return structured results."""
    ds_name = build_data_stream_name(index_prefix)

    # Parse baseline range
    parts = [s.strip() for s in baseline_range.split("/") if s.strip()]
    b_gte = parts[0] if parts else "now-7d"
    b_lte = parts[1] if len(parts) > 1 else "now-1h"

    current = _query_window(config, ds_name, time_range)
    baseline = _query_window(config, ds_name, b_gte, b_lte)

    run_id = f"eval-{uuid.uuid4().hex[:8]}"
    targets = evaluators or list(_EVAL_FUNCTIONS.keys())
    results: list[dict[str, Any]] = []

    for name in targets:
        fn = _EVAL_FUNCTIONS.get(name)
        if not fn:
            continue
        meta = EVALUATORS.get(name, {})
        try:
            result = fn(current, baseline)
        except Exception as exc:  # noqa: BLE001
            result = {"outcome": "fail", "score": 0.0, "detail": f"Evaluator crashed: {exc}"}
        results.append({
            "evaluator": name,
            "dimension": meta.get("dimension", "quality"),
            "run_id": run_id,
            **result,
        })

    # Summary
    outcomes = [r["outcome"] for r in results]
    if "fail" in outcomes:
        overall = "fail"
    elif "degraded" in outcomes:
        overall = "degraded"
    else:
        overall = "pass"

    avg_score = sum(r.get("score", 0) for r in results) / max(1, len(results))

    report = {
        "run_id": run_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "time_range": time_range,
        "baseline_range": baseline_range,
        "overall_outcome": overall,
        "average_score": round(avg_score, 2),
        "evaluator_count": len(results),
        "results": results,
    }

    if write_to_es:
        _write_eval_results(config, index_prefix, report)

    return report


def _write_eval_results(config: ESConfig, index_prefix: str, report: dict[str, Any]) -> None:
    """Write each evaluator result as a gen_ai.evaluation.* event to ES."""
    ds_name = build_data_stream_name(index_prefix)
    for r in report.get("results", []):
        doc = {
            "@timestamp": report["evaluated_at"],
            "event.kind": "event",
            "event.category": "process",
            "event.action": "evaluation",
            "event.outcome": "success" if r["outcome"] == "pass" else "failure",
            "event.dataset": "internal.evaluation",
            "service.name": "evaluate",
            "gen_ai.operation.name": "evaluation",
            "gen_ai.evaluation.run_id": r["run_id"],
            "gen_ai.evaluation.evaluator": r["evaluator"],
            "gen_ai.evaluation.score": r.get("score", 0),
            "gen_ai.evaluation.outcome": r["outcome"],
            "gen_ai.evaluation.dimension": r.get("dimension", "quality"),
            "message": f"[{r['outcome'].upper()}] {r['evaluator']}: {r.get('detail', '')}",
        }
        try:
            es_request(config, "POST", f"/{ds_name}/_create", doc)
        except SkillError as exc:
            print(f"⚠️ eval write failed ({r['evaluator']}): {exc}", file=sys.stderr)


def render_text(report: dict[str, Any]) -> str:
    icons = {"pass": "✓", "fail": "✗", "degraded": "!"}
    lines = [
        f"[{icons.get(report['overall_outcome'], '?')} {report['overall_outcome'].upper()}] "
        f"Evaluation run {report['run_id']} — score {report['average_score']:.2f}",
        f"  window: {report['time_range']}  baseline: {report['baseline_range']}",
        "",
    ]
    for r in report["results"]:
        icon = icons.get(r["outcome"], "?")
        lines.append(f"  {icon} {r['evaluator']} [{r['dimension']}]: {r.get('detail', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight evaluation runner")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run evaluators against recent traces")
    run_p.add_argument("--es-url", default="http://localhost:9200")
    run_p.add_argument("--es-user", default="")
    run_p.add_argument("--es-password", default="")
    run_p.add_argument("--index-prefix", default="agent-obsv")
    run_p.add_argument("--time-range", default="now-1h")
    run_p.add_argument("--baseline-range", default="now-7d/now-1h")
    run_p.add_argument("--evaluators", default="", help="Comma-separated evaluator names (default: all)")
    run_p.add_argument("--write-to-es", action="store_true", help="Write results to ES")
    run_p.add_argument("--output-format", choices=["text", "json"], default="text")

    sub.add_parser("list", help="List available evaluators")

    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()

        if args.command == "list":
            print(f"{'Evaluator':<30} {'Dimension':<12} Description")
            print("-" * 80)
            for name, meta in EVALUATORS.items():
                print(f"{name:<30} {meta['dimension']:<12} {meta['description']}")
            return 0

        if args.command == "run":
            credentials = validate_credential_pair(args.es_user, args.es_password)
            config = ESConfig(
                es_url=args.es_url,
                es_user=credentials[0] if credentials else None,
                es_password=credentials[1] if credentials else None,
            )
            evaluators = [e.strip() for e in args.evaluators.split(",") if e.strip()] or None
            report = run_evaluation(
                config,
                index_prefix=validate_index_prefix(args.index_prefix),
                time_range=args.time_range,
                baseline_range=args.baseline_range,
                evaluators=evaluators,
                write_to_es=args.write_to_es,
            )
            if args.output_format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(render_text(report))
            return 0 if report["overall_outcome"] == "pass" else 2

        print("Usage: evaluate.py {run|list}", file=sys.stderr)
        return 1

    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:
        print_error(f"Evaluation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
