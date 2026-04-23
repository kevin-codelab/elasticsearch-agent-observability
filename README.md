# elasticsearch-agent-observability

Elasticsearch backend for AI agent observability. One bootstrap gives you ES storage, Kibana dashboards, and automated RCA alerting.

Schema follows [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). Plug in [OpenLLMetry](https://github.com/traceloop/openllmetry) or any OTel instrumentation SDK — data lands in ES, dashboards light up.

## What you get

**One command, full ES stack:**

```
bootstrap_observability.py
 → index template + component templates + ILM (hot/warm/cold/delete)
 → ingest pipeline (OTel→ECS normalization, event.outcome derivation, sensitive field redaction)
 → Kibana dashboard (latency P50/P95, token usage, tool/model/session breakdown, failure hotspots)
 → OTel Collector config (spanmetrics + ES exporter)
 → OTLP HTTP bridge fallback path
```

**6 RCA alert analyzers:**

| Analyzer | Detects |
|----------|---------|
| `error_rate_spike` | error rate jump — pinpoints the tool / model / session |
| `token_consumption_anomaly` | token burn anomaly — finds the top-spending session and tool |
| `latency_degradation` | P95 latency regression — locates the slowest turn |
| `session_failure_hotspot` | failures concentrated in a few sessions |
| `retry_storm` | retry loop — finds the tool stuck in a cycle |
| `long_turn_hotspot` | single turn stuck — locates the blocking component |

When multiple alerts fire in the same window, they're merged into causal chains (`correlation.chains`) with confidence scores.

**Pipeline health diagnostic (`doctor.py`):**

Refuses to let `/healthz` lie. 5 independent checks — healthz, process/port state (with zombie detection), real ES data, OTLP canary — collapsed into one honest verdict:

- `healthy` — all clear
- `degraded_collector_path` — bridge fallback is saving you, Collector is down
- `broken` — data plane is dead (healthz may still say 200)
- `unreachable` — ES itself is down

**Zero-code ingestion path:**

Don't want to touch agent code? Generate an LLM proxy bundle (LiteLLM docker-compose), point the agent's `OPENAI_API_BASE` at it. Done.

## Data flow

```
Agent code
  │  pip install traceloop-sdk / or use the generated LLM proxy
  ▼
OTel SDK (gen_ai.* spans)
  │
  ▼
OTel Collector ──→ ES index template + ingest pipeline ──→ Kibana
  │                                                          │
  └── OTLP HTTP bridge (fallback) ─────────────────────────────┘
```

## Schema

OTel GenAI Semantic Conventions v1.40+ standard fields are used directly. Extension fields live under `gen_ai.agent_ext.*`:

| Field | Source |
|-------|--------|
| `gen_ai.request.model` | OTel standard |
| `gen_ai.tool.name` | OTel standard |
| `gen_ai.conversation.id` | OTel standard |
| `gen_ai.operation.name` | OTel standard |
| `gen_ai.usage.input_tokens` / `.output_tokens` | OTel standard |
| `error.type` | OTel/ECS standard |
| `gen_ai.agent_ext.turn_id` | extension (OTel proposal pending) |
| `gen_ai.agent_ext.component_type` | extension |
| `gen_ai.agent_ext.cost` | extension |
| `gen_ai.agent_ext.retry_count` | extension |

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md).

## Quick start

```bash
git clone https://github.com/kevin0x5/elasticsearch-agent-observability.git
cd elasticsearch-agent-observability

# Unified CLI (recommended)
python scripts/cli.py quickstart --agent-dir /path/to/your/agent \
  --es-url http://localhost:9200 --es-user elastic --es-password '<pwd>' \
  --apply --kibana-url http://localhost:5601

# Or the full bootstrap with all options
python scripts/bootstrap_observability.py \
  --workspace /path/to/your/agent \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --es-user elastic --es-password '<pwd>' \
  --apply-es-assets \
  --kibana-url http://localhost:5601 \
  --apply-kibana-assets
```

Hook up OpenLLMetry on the agent side:

```python
from traceloop.sdk import Traceloop
Traceloop.init()
```

Data flows into ES, Kibana dashboards are live.

## Unified CLI

```bash
python scripts/cli.py <command> [options]

# Available commands:
#   init        Bootstrap the full observability stack
#   quickstart  Guided one-command setup (auto-detects framework)
#   status      Report what assets are deployed on the cluster
#   doctor      Honest end-to-end pipeline diagnostic
#   alert       Alert check with intelligent root-cause analysis
#   cost        Model pricing, cost summary, and cost backfill
#   query       Pre-built ES query templates
#   report      Generate a smoke/metrics report
#   validate    Configuration drift detection
#   uninstall   Remove all managed assets from the cluster
#   scenarios   Show "I want to do X → run Y" cheat sheet
```

## Framework support

Auto-detected and instrumented via `quickstart` or `instrument_frameworks.py`:

| Framework | Runtime | Auto-detect | Zero-code path |
|-----------|---------|-------------|----------------|
| AutoGen | Python | ✓ | traceloop-sdk |
| CrewAI | Python | ✓ | traceloop-sdk |
| LangGraph / LangChain | Python | ✓ | traceloop-sdk |
| OpenAI Agents SDK | Python | ✓ | auto-patch |
| LlamaIndex | Python | ✓ | traceloop-sdk |
| OpenClaw | Node.js | ✓ | LLM proxy |
| Mastra | Node.js | ✓ | Node bootstrap |

## Commands

```bash
# Unified CLI (preferred)
python scripts/cli.py doctor --es-url <url>
python scripts/cli.py alert --es-url <url> --time-range now-15m
python scripts/cli.py cost summary --es-url <url> --time-range now-24h

# Direct script access (still works)
python scripts/doctor.py --es-url <url>
python scripts/alert_and_diagnose.py --es-url <url> --time-range now-15m

# Alert with external rules + Slack notification
python scripts/alert_and_diagnose.py --es-url <url> \
  --alert-rules rules.json \
  --webhook-url https://hooks.slack.com/... --webhook-template slack

# Cost analysis
python scripts/model_pricing.py summary --es-url <url>
python scripts/model_pricing.py enrich --es-url <url> --time-range now-7d
python scripts/model_pricing.py prices  # show built-in price table

# Config drift detection
python scripts/validate_state.py --es-url <url> --assets-dir generated/bootstrap/elasticsearch

# End-to-end canary
python scripts/verify_pipeline.py --es-url <url> --otlp-http-endpoint http://127.0.0.1:14319

# Uninstall
python scripts/uninstall.py --es-url <url> --confirm
```

## Requirements

- Python 3.10+ (stdlib only)
- Elasticsearch 8.x / 9.x + Kibana (Basic license)
- `otelcol-contrib` 0.87.0+ for the Collector path
- Any OTel GenAI instrumentation SDK (OpenLLMetry recommended)

## References

- [`references/instrumentation_contract.md`](references/instrumentation_contract.md) — field tiers
- [`references/telemetry_schema.md`](references/telemetry_schema.md) — full field dictionary
- [`references/post_bootstrap_playbook.md`](references/post_bootstrap_playbook.md) — post-bootstrap checklist
- [`references/config_guide.md`](references/config_guide.md) — operational contract

## License

Apache-2.0
