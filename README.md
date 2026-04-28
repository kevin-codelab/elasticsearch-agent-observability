# elasticsearch-agent-observability

A small toolkit for sending AI agent telemetry to Elasticsearch and reviewing it in Kibana.

Bootstrap generates an ES data stream, ingest pipeline, Kibana saved objects, ES|QL investigation queries, Query Rule templates, and optional evaluation/diagnosis helpers. It does not change Elasticsearch or Kibana source code; it only uses public APIs.

The schema follows [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) where they fit. Project-only fields stay under `gen_ai.agent_ext.*`.

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
python scripts/cli.py session-tail --output-dir ./generated/session-tail \
    --session-dir /path/to/agent/sessions --bridge-url http://localhost:14319
# Start tailing (runs alongside the agent, only new records by default):
python generated/session-tail/session_tail.py --from-end
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

If ES/Kibana are reachable and credentials are valid, telemetry should start landing in ES and Kibana assets should be available.

Then check pipeline health:

```bash
python scripts/cli.py doctor --es-url http://localhost:9200
```

## What you get

```
bootstrap_observability.py
 → index template + component templates + ILM (hot/warm/cold/delete)
 → ingest pipeline (OTel→ECS normalization, sensitive field redaction)
 → Kibana dashboard + Discover entries (event, failure, session, trace, MCP)
 → ES|QL investigation pack + Query Rule specs
 → OTel Collector config + OTLP HTTP bridge fallback
```

| Capability | What |
|------------|------|
| **Alerting** | 6 rule templates/checks for error spikes, token anomalies, latency regressions, session hotspots, retry storms, and slow turns |
| **Evaluation** | Local evaluators plus optional LLM-as-Judge; writes `gen_ai.evaluation.*` to ES when enabled |
| **User feedback** | `POST /v1/feedback` on the bridge, with score/sentiment fields for Kibana panels |
| **Reasoning trace** | Optional fields for decision rationale, alternatives, and confidence; no raw prompt capture by default |
| **Session view** | Renders a nested span tree from indexed traces when session/trace ids are present |
| **Pipeline diagnostic** | Checks ingest, mappings, Kibana assets, and field coverage; `/healthz` alone is not enough |
| **Framework support** | Detects common frameworks and generates wrappers/config where supported; coverage depends on runtime and instrumentation path |
| **Instrumentation coverage** | `doctor` reports missing fields and suggested fixes |

## Data flow

数据采集三层，**注入优先**：

```
Layer 1 — 旁路/预加载采集
┌─────────────────────────────────────────────────────┐
│  LLM Proxy (LiteLLM)                               │  ← agent 可不改代码
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

**原则**：latency、token、error 优先走 Layer 1。手动上报主要用于 reasoning/eval/feedback 等语义字段。

## CLI

```bash
python scripts/cli.py <command> [options]
```

| Command | What |
|---------|------|
| `init` | Generate assets and optionally apply them |
| `quickstart` | Guided setup (tries to detect framework) |
| `doctor` | Pipeline diagnostic + instrumentation coverage |
| `alert` | Alert checks + diagnosis (`--webhook-template slack\|dingtalk\|feishu\|wecom`) |
| `eval` | Run evaluators (`--evaluators llm_judge --llm-judge-endpoint <url>`) |
| `replay` | Session trace view (`--session-id <id>` or `--trace-id <id>`) |
| `session-tail` | Generate a session JSONL tail bundle |
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

The field dictionary covers OTel GenAI fields, ECS fields, and a small project extension namespace. Key namespaces:

| Namespace | Examples |
|-----------|----------|
| OTel GenAI | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.*`, `gen_ai.usage.*`, `gen_ai.conversation.id`, `gen_ai.tool.name` |
| OTel MCP | `mcp.method.name`, `mcp.session.id`, `mcp.resource.uri`, `gen_ai.prompt.name` |
| ECS standard | `@timestamp`, `event.*`, `service.*`, `trace.id`, `span.id` |
| Agent extensions | `gen_ai.agent_ext.reasoning.*`, `gen_ai.agent_ext.turn_id`, `gen_ai.agent_ext.component_type` |
| Evaluation | `gen_ai.evaluation.score`, `gen_ai.evaluation.outcome`, `gen_ai.evaluation.dimension` |
| Feedback | `gen_ai.feedback.score`, `gen_ai.feedback.sentiment`, `gen_ai.feedback.comment` |
| Guardrail | `gen_ai.guardrail.action`, `gen_ai.guardrail.category` |

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md). Field tiers: [`references/instrumentation_contract.md`](references/instrumentation_contract.md).

Sensitive GenAI content is off by default. The ingest pipeline and bridge remove `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments`, and `gen_ai.tool.call.result`. If you need payload capture, add an explicit opt-in path and redaction policy first.

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
