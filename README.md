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
| `gen_ai.agent_ext.reasoning.*` | extension — decision trace (action, rationale, alternatives, confidence) |
| `gen_ai.evaluation.*` | extension — eval results (evaluator, score, outcome, dimension) |
| `gen_ai.feedback.*` | extension — user feedback (score, sentiment, comment) |
| `gen_ai.guardrail.*` | extension — safety checks (action, category, rule_id) |

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
#   eval        Run regression evaluators (+ LLM-as-Judge) against recent traces
#   replay      Session replay — nested span tree with reasoning trace
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

## Evaluation & quality signals

**7 regression evaluators** (`agent-obsv eval run`):

| Evaluator | Dimension | What it checks |
|-----------|-----------|----------------|
| `latency_regression` | latency | P95 latency vs baseline |
| `error_rate_regression` | quality | Error rate vs baseline |
| `token_efficiency` | cost | Tokens per session vs baseline |
| `cost_regression` | cost | USD cost per session vs baseline |
| `tool_coverage` | quality | Fraction of known tools actually called |
| `guardrail_block_rate` | safety | Guardrail block/redact rate |
| `llm_judge` | quality | LLM-as-Judge via any OpenAI-compatible API |

```bash
# Rule-based evaluation
agent-obsv eval run --es-url <url> --write-to-es

# LLM-as-Judge (bring your own endpoint)
agent-obsv eval run --es-url <url> --evaluators llm_judge \
  --llm-judge-endpoint http://localhost:4000 --llm-judge-model gpt-4o-mini
```

**User feedback** — the OTLP HTTP bridge exposes `POST /v1/feedback`:

```bash
curl -X POST http://127.0.0.1:14319/v1/feedback \
  -H 'Content-Type: application/json' \
  -d '{"score": 1, "comment": "helpful", "trace_id": "abc", "session_id": "sess-1"}'
```

Feedback lands in the same data stream; Kibana shows sentiment distribution and score trend.

## Session replay

Reconstruct a nested span tree with reasoning trace and user feedback:

```bash
agent-obsv replay --es-url <url> --session-id <id>
agent-obsv replay --es-url <url> --trace-id <id> --format json
```

Output includes decision rationale at each step:
```
✓ [10:00:00Z] agent.run (runtime) 1200ms
  💭 decided=tool_call type=tool_selection conf=0.9 rejected=[web_search]
  📝 DB has the data the user needs
  ✓ [10:00:01Z] tool.query_db (tool) tool=query_db 800ms
  👤 score=1 sentiment=positive
```

## Reasoning trace

Record WHY the agent chose each action — not just what it did:

```python
from instrument_frameworks import traced_decision, emit_reasoning_span

@traced_decision(action="tool_call", decision_type="tool_selection",
                 rationale="User asked about weather", alternatives="web_search,cached")
def call_weather_api(city): ...

# Or standalone:
emit_reasoning_span(action="delegate", decision_type="delegation",
                    rationale="Task requires code expertise", confidence=0.85)
```

Fields: `gen_ai.agent_ext.reasoning.action`, `.alternatives`, `.rationale`, `.confidence`, `.decision_type`, `.step_index`.

## Commands

```bash
# Pipeline health (don't trust /healthz)
agent-obsv doctor --es-url <url>

# Alert + RCA (supports Slack/钉钉/飞书/企微 webhook templates)
agent-obsv alert --es-url <url> --time-range now-15m
agent-obsv alert --es-url <url> --alert-rules rules.json --webhook-url <url> --webhook-template slack

# Cost analysis (built-in price table: 30+ models)
agent-obsv cost summary --es-url <url>
agent-obsv cost enrich --es-url <url> --time-range now-7d
agent-obsv cost prices

# Evaluation
agent-obsv eval run --es-url <url> --write-to-es
agent-obsv eval list

# Session replay
agent-obsv replay --es-url <url> --session-id <id>

# Other
agent-obsv status --es-url <url>
agent-obsv validate --es-url <url> --assets-dir generated/bootstrap/elasticsearch
agent-obsv uninstall --es-url <url> --confirm
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
