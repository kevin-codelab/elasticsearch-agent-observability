# elasticsearch-agent-observability

Your agent is in production. A user reports a bad answer. Latency spikes. Token usage jumps. A tool starts failing.

If your team already uses Elasticsearch and Kibana, you should not need a new observability platform just to answer the first questions:

- Which session failed?
- Which tool or model caused it?
- Did token usage, latency, or retries change?
- Is telemetry reaching Elasticsearch at all?
- Can we inspect this in Kibana without storing raw prompts by default?

`elasticsearch-agent-observability` gives you that Elastic-native starting point for AI agent telemetry.

It does not modify Elasticsearch or Kibana. It renders assets and helper scripts that use public APIs: Elasticsearch data streams, ingest pipelines, index templates, Kibana saved objects, Discover, Lens, ES|QL, Query Rules, and OTLP.

## What you get after bootstrap

| Need | What this repo gives you |
|---|---|
| See agent traffic in Elastic | data stream, ECS/OTel GenAI mappings, ingest pipeline |
| Debug bad sessions | Discover searches, session drilldown, trace timeline |
| Find slow or failing components | Kibana Lens dashboard, tool/model/component breakdowns |
| Investigate token spikes | ES|QL queries for token, model, and session hotspots |
| Catch regressions | alert checks and Query Rule templates for error/latency/token/retry patterns |
| Check pipeline health | `doctor` canary, recent-data check, field coverage report |
| Connect existing agents | Python/Node starters, session-tail, LiteLLM proxy, Collector config |
| Keep sensitive payloads out | bridge + ingest redaction for prompts, messages, tool args/results |

This is the Elastic-side data layer and investigation pack. It is not an agent runtime, prompt manager, eval SaaS, or replacement for Kibana.

## Who this is for

Use it if:

- you already run Elasticsearch/Kibana, or plan to;
- you want agent observability in your existing Elastic stack;
- you prefer OTel GenAI-aligned fields over vendor-specific traces;
- you need practical session/tool/model/token visibility before building a full observability product.

Do not use it if you want hosted prompt management, annotation queues, experiment tracking, or a turnkey UI product. This repo intentionally stays close to Elastic primitives.

## Quick start

```bash
git clone https://github.com/kevin0x5/elasticsearch-agent-observability.git
cd elasticsearch-agent-observability

python scripts/cli.py quickstart \
  --agent-dir /path/to/your/agent \
  --es-url http://localhost:9200 --es-user elastic --es-password '<pwd>' \
  --apply --kibana-url http://localhost:5601
```

Then verify the path end to end:

```bash
python scripts/cli.py doctor --es-url http://localhost:9200
```

`doctor` does not trust `/healthz` alone. It checks listeners, recent ES data, canary ingestion, mappings, Kibana assets, and missing GenAI fields.

## Choose the least invasive ingest path

| Agent shape | Start here | Notes |
|---|---|---|
| Writes session JSONL | `session-tail` | good for OpenClaw, Mastra, custom runtimes with session files |
| Python agent | Python bootstrap + wrappers | use OTel/OpenLLMetry where possible; wrap custom tool/model calls |
| Node / TypeScript agent | `node --import` bootstrap + wrappers | preload captures HTTP; wrappers add GenAI semantic fields |
| Third-party agent you do not want to edit | LiteLLM proxy | works when the agent can use an OpenAI-compatible base URL |
| Existing OTel setup | Collector / direct OTLP | reuse what you already emit and normalize into the ES data stream |

Examples:

```bash
# Session JSONL → OTLP bridge
python scripts/cli.py session-tail \
  --output-dir ./generated/session-tail \
  --session-dir /path/to/agent/sessions \
  --bridge-url http://localhost:14319
python generated/session-tail/session_tail.py --from-end
```

```bash
# Node / TypeScript preload
python scripts/cli.py quickstart --agent-dir /path/to/agent --instrument-runtime node
node --import ./generated/node-instrumentation/agent-otel-bootstrap.mjs dist/index.js
```

```bash
# LLM proxy for compatible third-party agents
python scripts/cli.py init \
  --workspace /path/to/agent \
  --output-dir ./generated \
  --generate-llm-proxy \
  --apply-es-assets
cd generated/llm-proxy && docker compose up -d
export OPENAI_API_BASE=http://localhost:4000/v1
```

## What gets generated

`quickstart` / `init` can produce:

- Elasticsearch component templates, index template, ILM policy, and data stream
- ingest pipeline for OTel/ECS normalization and sensitive field redaction
- Kibana data view, dashboard, Lens panels, and Discover saved searches
- ES|QL investigation pack for slow answers, failed sessions, token spikes, MCP calls, and feedback
- Kibana Query Rule reference payloads
- OTel Collector config and OTLP HTTP bridge fallback
- Python / Node instrumentation starters
- session-tail bundle and optional LiteLLM proxy bundle
- detection evidence file explaining why quickstart chose a path

## Main commands

```bash
python scripts/cli.py <command> [options]
```

| Command | Use it for |
|---|---|
| `quickstart` | detect a known framework, generate assets, and explain the choice |
| `init` | generate/apply ES, Kibana, Collector, bridge, proxy, and instrumentation assets |
| `doctor` | check whether telemetry is flowing and which fields are missing |
| `session-tail` | generate a JSONL tailer for file-based agent sessions |
| `alert` | run field-based checks for error spikes, latency regressions, token anomalies, retry storms |
| `eval` | write lightweight regression evaluation events into ES |
| `replay` | render a session/trace tree from indexed spans |
| `status` | show which managed assets exist on the cluster |
| `validate` | compare generated assets with live cluster assets |
| `uninstall` | remove managed assets |

## Field contract

The schema follows [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) where they fit. Project-only fields stay under `gen_ai.agent_ext.*`.

The fields that matter first:

- session: `gen_ai.conversation.id`, `gen_ai.agent_ext.turn_id`
- tool/model: `gen_ai.tool.name`, `gen_ai.request.model`, `gen_ai.provider.name`
- operation: `gen_ai.operation.name` (`chat`, `invoke_agent`, `execute_tool`, etc.)
- cost/latency: `gen_ai.usage.*`, `event.duration`, `gen_ai.agent_ext.latency_ms`
- diagnosis: `event.outcome`, `error.type`, `gen_ai.agent_ext.retry_count`
- optional signals: `gen_ai.evaluation.*`, `gen_ai.feedback.*`, `gen_ai.guardrail.*`, MCP fields

Full dictionary: [`references/telemetry_schema.md`](references/telemetry_schema.md). Coverage tiers: [`references/instrumentation_contract.md`](references/instrumentation_contract.md).

## Privacy defaults

Raw GenAI payloads are off by default.

The bridge and ingest pipeline remove raw prompts, completions, chat messages, system instructions, tool definitions, tool arguments, and tool results. If you need payload capture, add an explicit opt-in path, truncation, PII filtering, and retention rules first.

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
