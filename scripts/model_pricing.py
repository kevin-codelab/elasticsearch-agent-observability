#!/usr/bin/env python3
"""Built-in model pricing table and cost calculation.

Computes gen_ai.agent_ext.cost from token usage and a price table.
Can be used as:
  1. A post-hoc enrichment script that backfills cost into ES docs
  2. A library imported by the instrument snippet for real-time cost tagging
  3. A standalone query tool: `python model_pricing.py --es-url ... --time-range now-1h`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
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
# Built-in price table (USD per 1M tokens, as of 2025-Q1)
# Format: model_pattern -> (input_price_per_1M, output_price_per_1M)
# Patterns are matched with startswith, most specific first.
# ---------------------------------------------------------------------------

DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini":          (0.15,   0.60),
    "gpt-4o":               (2.50,  10.00),
    "gpt-4-turbo":          (10.00, 30.00),
    "gpt-4":                (30.00, 60.00),
    "gpt-3.5-turbo":        (0.50,   1.50),
    "o1-mini":              (3.00,  12.00),
    "o1":                   (15.00, 60.00),
    "o3-mini":              (1.10,   4.40),
    "o3":                   (10.00, 40.00),
    "o4-mini":              (1.10,   4.40),
    # Anthropic
    "claude-3-5-sonnet":    (3.00,  15.00),
    "claude-3-5-haiku":     (0.80,   4.00),
    "claude-3-opus":        (15.00, 75.00),
    "claude-3-sonnet":      (3.00,  15.00),
    "claude-3-haiku":       (0.25,   1.25),
    "claude-sonnet-4":      (3.00,  15.00),
    "claude-opus-4":        (15.00, 75.00),
    # Google
    "gemini-2.5-pro":       (1.25,  10.00),
    "gemini-2.5-flash":     (0.15,   0.60),
    "gemini-2.0-flash":     (0.10,   0.40),
    "gemini-1.5-pro":       (1.25,   5.00),
    "gemini-1.5-flash":     (0.075,  0.30),
    # DeepSeek
    "deepseek-chat":        (0.27,   1.10),
    "deepseek-reasoner":    (0.55,   2.19),
    # Meta / open-source (typical hosted pricing)
    "llama-3.1-405b":       (3.00,   3.00),
    "llama-3.1-70b":        (0.80,   0.80),
    "llama-3.1-8b":         (0.10,   0.10),
    # Mistral
    "mistral-large":        (2.00,   6.00),
    "mistral-small":        (0.20,   0.60),
    "mistral-nemo":         (0.15,   0.15),
    # Qwen
    "qwen-max":             (1.60,   6.40),
    "qwen-plus":            (0.40,   1.20),
    "qwen-turbo":           (0.06,   0.20),
}


def load_prices(custom_path: str | None = None) -> dict[str, tuple[float, float]]:
    """Load the price table, optionally merging a custom JSON file.

    Custom file format:
    {
      "model-name": {"input": 1.5, "output": 5.0},
      ...
    }
    """
    prices = dict(DEFAULT_PRICES)
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.is_file():
            try:
                custom = json.loads(p.read_text(encoding="utf-8"))
                for model, entry in custom.items():
                    if isinstance(entry, dict):
                        prices[model] = (float(entry.get("input", 0)), float(entry.get("output", 0)))
                    elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        prices[model] = (float(entry[0]), float(entry[1]))
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"⚠️ Failed to parse custom prices {p}: {exc}", file=sys.stderr)
    return prices


def match_price(model: str, prices: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Match a model name to its price. Returns (input_per_1M, output_per_1M) or None."""
    model_lower = model.lower().strip()
    # Exact match first
    if model_lower in prices:
        return prices[model_lower]
    # Prefix match (longest prefix wins)
    best_match = None
    best_len = 0
    for pattern, price in prices.items():
        if model_lower.startswith(pattern) and len(pattern) > best_len:
            best_match = price
            best_len = len(pattern)
    return best_match


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    prices: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """Compute USD cost for a single call. Returns None if model not in table."""
    if prices is None:
        prices = DEFAULT_PRICES
    price = match_price(model, prices)
    if price is None:
        return None
    input_cost = (input_tokens / 1_000_000) * price[0]
    output_cost = (output_tokens / 1_000_000) * price[1]
    return round(input_cost + output_cost, 8)


# ---------------------------------------------------------------------------
# ES enrichment: backfill gen_ai.agent_ext.cost for existing docs
# ---------------------------------------------------------------------------

def enrich_costs(
    config: ESConfig,
    *,
    index_prefix: str,
    time_range: str = "now-24h",
    prices: dict[str, tuple[float, float]] | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Backfill gen_ai.agent_ext.cost for docs that have token counts but no cost.

    Uses scroll + bulk update. Only touches docs where:
    - gen_ai.usage.input_tokens exists
    - gen_ai.request.model exists
    - gen_ai.agent_ext.cost is missing or null
    """
    if prices is None:
        prices = DEFAULT_PRICES

    ds_name = f"{build_data_stream_name(index_prefix)}*"
    query = {
        "bool": {
            "filter": [
                {"range": {"@timestamp": {"gte": time_range}}},
                {"exists": {"field": "gen_ai.usage.input_tokens"}},
                {"exists": {"field": "gen_ai.request.model"}},
            ],
            "must_not": [
                {"exists": {"field": "gen_ai.agent_ext.cost"}},
            ],
        }
    }

    # Count first
    try:
        count_resp = es_request(config, "POST", f"/{ds_name}/_count", {"query": query})
        total = count_resp.get("count", 0)
    except SkillError as exc:
        return {"status": "error", "detail": str(exc), "enriched": 0}

    if total == 0:
        return {"status": "ok", "detail": "No docs need cost enrichment", "enriched": 0, "total_scanned": 0}

    if dry_run:
        return {"status": "dry_run", "detail": f"{total} doc(s) would be enriched", "enriched": 0, "total_scanned": total}

    # Scroll and enrich
    enriched = 0
    unpriced_models: set[str] = set()
    search_body = {
        "size": batch_size,
        "query": query,
        "_source": ["gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.request.model"],
        "sort": ["_doc"],
    }

    try:
        resp = es_request(config, "POST", f"/{ds_name}/_search", search_body)
    except SkillError as exc:
        return {"status": "error", "detail": str(exc), "enriched": 0}

    while True:
        hits = (resp.get("hits") or {}).get("hits", [])
        if not hits:
            break

        bulk_lines: list[str] = []
        for hit in hits:
            src = hit.get("_source", {})
            model = str(src.get("gen_ai.request.model", "")).strip()
            input_tokens = int(src.get("gen_ai.usage.input_tokens", 0) or 0)
            output_tokens = int(src.get("gen_ai.usage.output_tokens", 0) or 0)
            cost = compute_cost(input_tokens, output_tokens, model, prices)
            if cost is None:
                unpriced_models.add(model)
                continue
            # Data streams require update by query, not direct _update.
            bulk_lines.append(json.dumps({"update": {"_id": hit["_id"], "_index": hit["_index"]}}))
            bulk_lines.append(json.dumps({"doc": {"gen_ai.agent_ext.cost": cost}}))
            enriched += 1

        if bulk_lines:
            bulk_body = "\n".join(bulk_lines) + "\n"
            try:
                es_request(config, "POST", "/_bulk", None)
                # Use raw request for bulk
                import urllib.request
                import base64
                url = config.es_url.rstrip("/") + "/_bulk"
                req = urllib.request.Request(url, data=bulk_body.encode("utf-8"), method="POST")
                req.add_header("Content-Type", "application/x-ndjson")
                if config.es_user and config.es_password:
                    token = base64.b64encode(f"{config.es_user}:{config.es_password}".encode()).decode()
                    req.add_header("Authorization", f"Basic {token}")
                with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
                    _ = response.read()
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ bulk update failed: {exc}", file=sys.stderr)

        # Next page
        if len(hits) < batch_size:
            break
        last_sort = hits[-1].get("sort")
        if not last_sort:
            break
        search_body["search_after"] = last_sort
        try:
            resp = es_request(config, "POST", f"/{ds_name}/_search", search_body)
        except SkillError:
            break

    return {
        "status": "ok",
        "enriched": enriched,
        "total_scanned": total,
        "unpriced_models": sorted(unpriced_models) if unpriced_models else [],
    }


# ---------------------------------------------------------------------------
# Cost summary query
# ---------------------------------------------------------------------------

def cost_summary(
    config: ESConfig,
    *,
    index_prefix: str,
    time_range: str = "now-24h",
) -> dict[str, Any]:
    """Query cost breakdown by model, tool, and session."""
    ds_name = f"{build_data_stream_name(index_prefix)}*"
    payload = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": time_range}}},
                    {"exists": {"field": "gen_ai.agent_ext.cost"}},
                ],
                "must_not": [{"prefix": {"event.dataset": "internal."}}],
            }
        },
        "aggs": {
            "total_cost": {"sum": {"field": "gen_ai.agent_ext.cost"}},
            "by_model": {
                "terms": {"field": "gen_ai.request.model", "size": 20, "order": {"cost": "desc"}},
                "aggs": {
                    "cost": {"sum": {"field": "gen_ai.agent_ext.cost"}},
                    "calls": {"value_count": {"field": "gen_ai.agent_ext.cost"}},
                },
            },
            "by_tool": {
                "terms": {"field": "gen_ai.tool.name", "size": 20, "order": {"cost": "desc"}},
                "aggs": {"cost": {"sum": {"field": "gen_ai.agent_ext.cost"}}},
            },
            "by_session": {
                "terms": {"field": "gen_ai.conversation.id", "size": 10, "order": {"cost": "desc"}},
                "aggs": {"cost": {"sum": {"field": "gen_ai.agent_ext.cost"}}},
            },
        },
    }
    try:
        result = es_request(config, "POST", f"/{ds_name}/_search", payload)
    except SkillError as exc:
        return {"status": "error", "detail": str(exc)}

    aggs = result.get("aggregations", {})
    total = aggs.get("total_cost", {}).get("value", 0) or 0

    def _extract(agg_name: str) -> list[dict[str, Any]]:
        buckets = aggs.get(agg_name, {}).get("buckets", [])
        return [
            {
                "name": b.get("key", "unknown"),
                "cost": round(b.get("cost", {}).get("value", 0) or 0, 6),
                "calls": b.get("calls", {}).get("value") or b.get("doc_count", 0),
            }
            for b in buckets
        ]

    return {
        "status": "ok",
        "time_range": time_range,
        "total_cost_usd": round(total, 6),
        "by_model": _extract("by_model"),
        "by_tool": _extract("by_tool"),
        "by_session": _extract("by_session"),
    }


def render_cost_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"❌ Cost query failed: {result.get('detail', 'unknown')}"
    lines = [
        f"💰 Total cost: ${result['total_cost_usd']:.4f} USD ({result['time_range']})",
        "",
    ]
    if result.get("by_model"):
        lines.append("By model:")
        for m in result["by_model"]:
            lines.append(f"  {m['name']:<30} ${m['cost']:.6f}  ({m['calls']} calls)")
    if result.get("by_tool"):
        lines.append("")
        lines.append("By tool:")
        for t in result["by_tool"]:
            lines.append(f"  {t['name']:<30} ${t['cost']:.6f}")
    if result.get("by_session"):
        lines.append("")
        lines.append("Top sessions by cost:")
        for s in result["by_session"]:
            lines.append(f"  {s['name']:<30} ${s['cost']:.6f}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Model pricing and cost analysis")
    sub = parser.add_subparsers(dest="command")

    # Cost summary
    summary = sub.add_parser("summary", help="Query cost breakdown")
    summary.add_argument("--es-url", default="http://localhost:9200")
    summary.add_argument("--es-user", default="")
    summary.add_argument("--es-password", default="")
    summary.add_argument("--index-prefix", default="agent-obsv")
    summary.add_argument("--time-range", default="now-24h")
    summary.add_argument("--output-format", choices=["text", "json"], default="text")

    # Enrich (backfill)
    enrich = sub.add_parser("enrich", help="Backfill gen_ai.agent_ext.cost into ES docs")
    enrich.add_argument("--es-url", default="http://localhost:9200")
    enrich.add_argument("--es-user", default="")
    enrich.add_argument("--es-password", default="")
    enrich.add_argument("--index-prefix", default="agent-obsv")
    enrich.add_argument("--time-range", default="now-24h")
    enrich.add_argument("--custom-prices", default="", help="Optional JSON file with custom model prices")
    enrich.add_argument("--dry-run", action="store_true")

    # List prices
    sub.add_parser("prices", help="Print the built-in price table")

    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.command == "prices":
            print(f"{'Model':<30} {'Input/1M':>10} {'Output/1M':>10}")
            print("-" * 52)
            for model, (inp, out) in sorted(DEFAULT_PRICES.items()):
                print(f"{model:<30} ${inp:>9.3f} ${out:>9.3f}")
            return 0

        if args.command == "summary":
            credentials = validate_credential_pair(args.es_user, args.es_password)
            config = ESConfig(
                es_url=args.es_url,
                es_user=credentials[0] if credentials else None,
                es_password=credentials[1] if credentials else None,
            )
            result = cost_summary(config, index_prefix=validate_index_prefix(args.index_prefix), time_range=args.time_range)
            if args.output_format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(render_cost_text(result))
            return 0

        if args.command == "enrich":
            credentials = validate_credential_pair(args.es_user, args.es_password)
            config = ESConfig(
                es_url=args.es_url,
                es_user=credentials[0] if credentials else None,
                es_password=credentials[1] if credentials else None,
            )
            prices = load_prices(args.custom_prices or None)
            result = enrich_costs(
                config,
                index_prefix=validate_index_prefix(args.index_prefix),
                time_range=args.time_range,
                prices=prices,
                dry_run=args.dry_run,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        print("Usage: model_pricing.py {summary|enrich|prices}", file=sys.stderr)
        return 1

    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:
        print_error(f"Failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
