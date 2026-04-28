# elasticsearch-agent-observability

Elasticsearch backend for AI agent observability. One bootstrap → ES storage + Kibana dashboards + RCA alerting + regression evaluation.

Schema follows [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). Plug in [OpenLLMetry](https://github.com/traceloop/openllmetry) or any OTel SDK — data lands in ES, dashboards light up.

**Python 3.10+, stdlib only, Basic (free) ES license.**

## Quick start

```bash
git clone https://github.com/kevin0x5/elasticsearch-agent-observability.git
cd elasticsearch-agent-observability

python scripts/cli.py quickstart \
  --agent-dir /path/to/your/agent \
  --es-url http://localhost:9200 --es-user elastic --es-password '<pwd>' \
  --apply --kibana-url http://localhost:5601
```

Agent side — pick the path that fits your runtime:

**Python agents** (CrewAI, LangGraph, AutoGen, LlamaIndex, custom):
```python
pip install traceloop-sdk
```
```python
from traceloop.sdk import Traceloop
Traceloop.init()
```

**Node.js / TypeScript agents** (OpenClaw, Mastra, custom):
```bash
# Generate the Node bootstrap during quickstart:
python scripts/cli.py quickstart --agent-dir /path/to/agent --generate-instrument-snippet --instrument-runtime node
# Then preload it:
node --import ./generated/node-instrumentation/agent-otel-bootstrap.mjs dist/index.js
```

**Any language / can't touch code** (Hermes, upstream OSS agents):
```bash
# Generate a LLM proxy (LiteLLM docker-compose):
python scripts/cli.py init --workspace /path/to/agent --output-dir ./generated --generate-llm-proxy --apply-es-assets
cd generated/llm-proxy && docker compose up -d
# Point the agent at the proxy:
export OPENAI_API_BASE=http://localhost:4000/v1
```

Done. Data flows into ES, Kibana dashboards are live.

Then check pipeline health:

```bash
python scripts/cli.py doctor --es-url http://localhost:9200
```

## What you get

```
bootstrap_observability.py
 → index template + component templates + ILM (hot/warm/cold/delete)
 → ingest pipeline (OTel→ECS normalization, sensitive field redaction)
 → 22 Kibana panels (latency, tokens, cost, tools, sessions, guardrail, eval, feedback, reasoning)
 → OTel Collector config + OTLP HTTP bridge fallback
```

| Capability | What |
|------------|------|
| **Alerting** | 6 RCA analyzers (error spike, token anomaly, latency regression, session hotspot, retry storm, slow turn) with causal chain merging |
| **Evaluation** | 7 regression evaluators + LLM-as-Judge, writes `gen_ai.evaluation.*` to ES |
| **Cost tracking** | Built-in price table (30+ models), cost summary, cost backfill |
| **User feedback** | `POST /v1/feedback` on the bridge, sentiment + score trend panels |
| **Reasoning trace** | Records why the agent chose each action (rationale, alternatives, confidence) |
| **Session replay** | Nested span tree with decision trail + feedback at each step |
| **Pipeline diagnostic** | 5 independent checks, refuses to let `/healthz` lie |
| **Framework support** | Auto-detect + instrument AutoGen, CrewAI, LangGraph, OpenAI Agents, LlamaIndex, OpenClaw, Mastra |
| **Instrumentation coverage** | Doctor tells you which fields are missing + exact fix snippets |

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

## CLI

```bash
python scripts/cli.py <command> [options]
```

| Command | What |
|---------|------|
| `init` | Bootstrap the full stack |
| `quickstart` | Guided setup (auto-detects framework) |
| `doctor` | Pipeline diagnostic + instrumentation coverage |
| `alert` | Alert + RCA (`--webhook-template slack\|dingtalk\|feishu\|wecom`) |
| `eval` | Run evaluators (`--evaluators llm_judge --llm-judge-endpoint <url>`) |
| `replay` | Session replay (`--session-id <id>` or `--trace-id <id>`) |
| `cost` | Cost summary / enrich / prices |
| `status` | What's deployed on the cluster |
| `validate` | Config drift detection |
| `uninstall` | Remove all managed assets |
| `scenarios` | "I want to do X → run Y" cheat sheet |

## Schema

80+ fields across OTel GenAI standard, ECS, and project extensions. Key namespaces:

| Namespace | Examples |
|-----------|----------|
| OTel standard | `gen_ai.request.model`, `gen_ai.tool.name`, `gen_ai.usage.*`, `gen_ai.conversation.id` |
| ECS standard | `@timestamp`, `event.*`, `service.*`, `trace.id`, `span.id` |
| Agent extensions | `gen_ai.agent_ext.reasoning.*`, `gen_ai.agent_ext.cost`, `gen_ai.agent_ext.turn_id` |
| Evaluation | `gen_ai.evaluation.score`, `gen_ai.evaluation.outcome`, `gen_ai.evaluation.dimension` |
| Feedback | `gen_ai.feedback.score`, `gen_ai.feedback.sentiment`, `gen_ai.feedback.comment` |
| Guardrail | `gen_ai.guardrail.action`, `gen_ai.guardrail.category` |

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md). Field tiers: [`references/instrumentation_contract.md`](references/instrumentation_contract.md).

## Requirements

- Python 3.10+ (stdlib only, zero third-party deps)
- Elasticsearch 8.x / 9.x + Kibana (Basic license)
- `otelcol-contrib` 0.87.0+ for the Collector path
- Any OTel GenAI instrumentation SDK (OpenLLMetry recommended)

## References

- [`references/instrumentation_contract.md`](references/instrumentation_contract.md) — field tiers (Tier 1/2/3)
- [`references/telemetry_schema.md`](references/telemetry_schema.md) — full field dictionary
- [`references/post_bootstrap_playbook.md`](references/post_bootstrap_playbook.md) — post-bootstrap checklist
- [`references/config_guide.md`](references/config_guide.md) — operational contract
- [`references/credentials_playbook.md`](references/credentials_playbook.md) — credential security

## License

Apache-2.0
