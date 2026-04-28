# elasticsearch-agent-observability

Use Elasticsearch + Kibana to observe AI agents without patching Elasticsearch or Kibana.

This repo gives you the Elastic-side pieces for agent telemetry: data stream, mappings, ingest pipeline, Kibana dashboard/searches, ES|QL investigation queries, query-rule templates, and small helper scripts for diagnosis/evaluation.

It is useful when you already have, or are willing to run, Elasticsearch/Kibana and want to answer questions like:

| Question | Where this repo helps |
|---|---|
| Which agent session failed, and where did it fail? | Discover searches, session drilldown, trace timeline |
| Which tool/model is slow or noisy? | Kibana dashboard, ES|QL investigations, alert checks |
| Why did token usage jump? | Token panels, token-spike ES|QL query, model/session breakdown |
| Is telemetry actually reaching ES? | `doctor`, canary check, field coverage report |
| How do I connect an existing agent with minimal changes? | session-tail, LLM proxy, Python/Node instrumentation starters |
| Can I keep prompts/tool args out of ES? | bridge + ingest redaction defaults |

**Python 3.10+, stdlib scripts, Elasticsearch/Kibana Basic license.**

## What it installs

`quickstart` / `init` can generate and optionally apply:

- Elasticsearch data stream, component templates, index template, ILM policy
- ingest pipeline for OTel/ECS normalization and sensitive field redaction
- Kibana data view, dashboard, Lens panels, and Discover saved searches
- ES|QL investigation pack for slow answers, failed sessions, token spikes, MCP calls, and feedback
- Kibana Query Rule reference payloads
- OTel Collector config and OTLP HTTP bridge fallback
- Python / Node instrumentation starters, session-tail bundle, and optional LLM proxy bundle

It uses public APIs only. No Elasticsearch or Kibana source changes.

## Quick start

```bash
git clone https://github.com/kevin0x5/elasticsearch-agent-observability.git
cd elasticsearch-agent-observability

python scripts/cli.py quickstart \
  --agent-dir /path/to/your/agent \
  --es-url http://localhost:9200 --es-user elastic --es-password '<pwd>' \
  --apply --kibana-url http://localhost:5601
```

Then verify the pipeline:

```bash
python scripts/cli.py doctor --es-url http://localhost:9200
```

`doctor` checks more than `/healthz`: listener state, recent ES data, canary ingestion, mappings, Kibana assets, and missing GenAI fields.

## Pick an ingestion path

| Agent shape | Recommended path | What you get |
|---|---|---|
| Agent writes session JSONL | `session-tail` | session/model/tool/tokens from files, no agent code changes when the JSONL shape matches |
| Python agent | generated Python bootstrap + wrappers | OTel spans/logs, model/tool/session fields where wrappers are used |
| Node / TypeScript agent | `node --import` bootstrap + wrappers | HTTP spans from preload, GenAI fields from wrappers |
| Third-party agent you do not want to edit | LiteLLM proxy | model latency/tokens/errors for compatible OpenAI-style calls |
| Existing OTel setup | Collector config / direct OTLP | reuse current telemetry and normalize into the ES data stream |

Session-tail example:

```bash
python scripts/cli.py session-tail \
  --output-dir ./generated/session-tail \
  --session-dir /path/to/agent/sessions \
  --bridge-url http://localhost:14319

python generated/session-tail/session_tail.py --from-end
```

Python example:

```bash
pip install traceloop-sdk
```

```python
from traceloop.sdk import Traceloop
Traceloop.init()
```

Node example:

```bash
python scripts/cli.py quickstart --agent-dir /path/to/agent --instrument-runtime node
node --import ./generated/node-instrumentation/agent-otel-bootstrap.mjs dist/index.js
```

Proxy example:

```bash
python scripts/cli.py init \
  --workspace /path/to/agent \
  --output-dir ./generated \
  --generate-llm-proxy \
  --apply-es-assets

cd generated/llm-proxy && docker compose up -d
export OPENAI_API_BASE=http://localhost:4000/v1
```

## Main commands

```bash
python scripts/cli.py <command> [options]
```

| Command | Use it for |
|---|---|
| `quickstart` | detect a known framework, generate assets, and print why it chose that path |
| `init` | generate/apply ES, Kibana, Collector, bridge, proxy, and instrumentation assets |
| `doctor` | check whether the telemetry pipeline is alive and which fields are missing |
| `session-tail` | generate a JSONL tailer for file-based agent sessions |
| `alert` | run field-based checks for error spikes, latency regressions, token anomalies, retry storms |
| `eval` | write lightweight regression evaluation events into ES |
| `replay` | render a session/trace tree from indexed spans |
| `status` | show which managed assets exist on the cluster |
| `validate` | compare generated assets with live cluster assets |
| `uninstall` | remove managed assets |

## Data contract

The schema follows [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) where they fit. Project-only fields stay under `gen_ai.agent_ext.*`.

Important fields:

- model/provider: `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`
- token usage: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, cache token fields
- session/turn: `gen_ai.conversation.id`, `gen_ai.agent_ext.turn_id`
- tool/MCP: `gen_ai.tool.name`, `gen_ai.operation.name=execute_tool`, `mcp.method.name`
- diagnosis: `event.outcome`, `error.type`, `gen_ai.agent_ext.retry_count`, `gen_ai.agent_ext.latency_ms`
- optional product signals: `gen_ai.evaluation.*`, `gen_ai.feedback.*`, `gen_ai.guardrail.*`

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md). Field tiers: [`references/instrumentation_contract.md`](references/instrumentation_contract.md).

## Privacy defaults

Raw GenAI payloads are off by default.

The bridge and ingest pipeline remove raw prompts, completions, chat messages, system instructions, tool definitions, tool arguments, and tool results. If you need payload capture, add an explicit opt-in path, truncation, PII filtering, and retention rules first.

## Boundaries

This repo is not an agent runtime, prompt manager, eval platform, or full observability SaaS.

It provides an Elastic-native data layer and starter investigation surface. The quality of the dashboard depends on what your agent emits. `doctor` will tell you which fields are missing.

## Requirements

- Python 3.10+
- Elasticsearch 8.x / 9.x
- Kibana 8.14+ / 9.x for generated Lens saved objects
- `otelcol-contrib` 0.87.0+ if you use the Collector path
- Optional: OTel GenAI instrumentation SDK such as OpenLLMetry

## References

- [`references/instrumentation_contract.md`](references/instrumentation_contract.md) — field tiers and what powers each view
- [`references/telemetry_schema.md`](references/telemetry_schema.md) — field dictionary
- [`references/post_bootstrap_playbook.md`](references/post_bootstrap_playbook.md) — what to wire after bootstrap
- [`references/config_guide.md`](references/config_guide.md) — operational contract
- [`references/credentials_playbook.md`](references/credentials_playbook.md) — credential handling

## License

Apache-2.0
