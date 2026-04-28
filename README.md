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

**Agent writes session JSONL** (OpenClaw, Mastra, custom runtimes with session files):
```bash
# Generate the session tail script during quickstart:
python scripts/render_session_tail.py --output-dir ./generated/session-tail \
    --session-dir /path/to/agent/sessions --bridge-url http://localhost:14319
# Start tailing (runs alongside the agent):
python generated/session-tail/session_tail.py
```

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
 → 20 Kibana panels (latency, tokens, tools, sessions, guardrail, eval, feedback, reasoning)
 → OTel Collector config + OTLP HTTP bridge fallback
```

| Capability | What |
|------------|------|
| **Alerting** | 6 RCA analyzers (error spike, token anomaly, latency regression, session hotspot, retry storm, slow turn) with causal chain merging |
| **Evaluation** | 7 regression evaluators + LLM-as-Judge, writes `gen_ai.evaluation.*` to ES |
| **User feedback** | `POST /v1/feedback` on the bridge, sentiment + score trend panels |
| **Reasoning trace** | Records why the agent chose each action (rationale, alternatives, confidence) |
| **Session replay** | Nested span tree with decision trail + feedback at each step |
| **Pipeline diagnostic** | 5 independent checks, refuses to let `/healthz` lie |
| **Framework support** | Auto-detect + instrument AutoGen, CrewAI, LangGraph, OpenAI Agents, LlamaIndex, OpenClaw, Mastra |
| **Instrumentation coverage** | Doctor tells you which fields are missing + exact fix snippets |

## Data flow

数据采集三层，**注入优先**：

```
Layer 1 — 自动注入（zero-code）
┌─────────────────────────────────────────────────────┐
│  LLM Proxy (LiteLLM)                               │  ← 最干净，agent 零改动
│  Python OTel Bootstrap (monkey-patch OpenAI/Anthropic)
│  Node.js OTel Bootstrap (--import preload)          │
│  覆盖：model, tokens, latency, error                │
└─────────────────────────────────────────────────────┘
         │
Layer 2 — 框架注入（low-code）
┌─────────────────────────────────────────────────────┐
│  instrument_frameworks.py (AutoGen/CrewAI/LangGraph)│
│  覆盖：session_id, agent_name, tool_name, turn      │
└─────────────────────────────────────────────────────┘
         │
Layer 3 — 主动上报（agent-specific，仅用于不可注入的数据）
┌─────────────────────────────────────────────────────┐
│  traced_decision() / emit_reasoning_span()          │  ← reasoning
│  evaluate.py run                                    │  ← evaluation
│  POST /v1/feedback                                  │  ← user feedback
└─────────────────────────────────────────────────────┘
         │
         ▼
OTel Collector / OTLP HTTP Bridge → ES ingest pipeline → Kibana
```

**原则**：如果 agent 在手动 emit latency 或 token，说明应该走 Layer 1。手动上报只用于 reasoning/eval/feedback。

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
| `status` | What's deployed on the cluster |
| `validate` | Config drift detection |
| `uninstall` | Remove all managed assets |
| `scenarios` | "I want to do X → run Y" cheat sheet |

## User Feedback

The OTLP HTTP bridge exposes a dedicated `POST /v1/feedback` endpoint. No OTLP wrapping needed — just plain JSON:

```bash
curl -X POST http://localhost:14319/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "score": 5,
    "sentiment": "positive",
    "comment": "Great answer",
    "session_id": "sess-001",
    "trace_id": "abc123",
    "user_id": "user-42"
  }'
```

All fields except `score` are optional. If `sentiment` is omitted, it's auto-derived from `score` (positive > 0, negative < 0, neutral = 0). Data lands in the same ES data stream and powers the Feedback Sentiment / Feedback Score dashboard panels.

## Schema

80+ fields across OTel GenAI standard, ECS, and project extensions. Key namespaces:

| Namespace | Examples |
|-----------|----------|
| OTel standard | `gen_ai.request.model`, `gen_ai.tool.name`, `gen_ai.usage.*`, `gen_ai.conversation.id` |
| ECS standard | `@timestamp`, `event.*`, `service.*`, `trace.id`, `span.id` |
| Agent extensions | `gen_ai.agent_ext.reasoning.*`, `gen_ai.agent_ext.turn_id`, `gen_ai.agent_ext.component_type` |
| Evaluation | `gen_ai.evaluation.score`, `gen_ai.evaluation.outcome`, `gen_ai.evaluation.dimension` |
| Feedback | `gen_ai.feedback.score`, `gen_ai.feedback.sentiment`, `gen_ai.feedback.comment` |
| Guardrail | `gen_ai.guardrail.action`, `gen_ai.guardrail.category` |

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md). Field tiers: [`references/instrumentation_contract.md`](references/instrumentation_contract.md).

## Requirements

- Python 3.10+ (stdlib only, zero third-party deps)
- Elasticsearch 8.x / 9.x + Kibana 8.14+ / 9.x (Basic license; Lens panels require Kibana 8.14+)
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
