# Telemetry Schema

## Primary contract

This repo targets a **9.x ECS + OTel GenAI Semantic Conventions** ingest contract.

Fields are split into three namespaces:

- **OTel GenAI standard** — `gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.conversation.id`, `gen_ai.tool.name`, `gen_ai.operation.name`, `gen_ai.agent.id/name/version`, `error.type`
- **ECS standard** — `@timestamp`, `event.*`, `service.*`, `agent.*`, `trace.id`, `span.id`, `parent.id`, `transaction.id`
- **Project extensions** — `gen_ai.agent_ext.*` (fields awaiting OTel SemConv proposal)

Send canonical fields directly:

- `@timestamp`
- `event.*`
- `service.*`
- `agent.*`
- `trace.id`, `span.id`, `parent.id`, `transaction.id`
- `gen_ai.provider.name`, `gen_ai.system`
- `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.output.type`
- `gen_ai.operation.name`
- `gen_ai.usage.*`, including `gen_ai.usage.cache_read.input_tokens` and `gen_ai.usage.cache_creation.input_tokens`
- `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.agent.version`, `gen_ai.agent.description`
- `gen_ai.conversation.id`
- `gen_ai.prompt.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`
- `gen_ai.data_source.id`
- `mcp.method.name`, `mcp.session.id`, `mcp.resource.uri`
- `error.type`
- `gen_ai.agent_ext.*`
- `gen_ai.guardrail.*`
- `gen_ai.evaluation.*`

## Component type tagging

Use `gen_ai.agent_ext.component_type` to tag spans with their component category:

- `runtime` — agent runtime entrypoint
- `llm` — model inference call
- `tool` — tool execution
- `mcp` — MCP protocol call
- `memory` — memory store read/write
- `knowledge` — knowledge base / RAG retrieval
- `guardrail` — safety check / content filter

## Extension fields (`gen_ai.agent_ext.*`)

These fields extend OTel GenAI Semantic Conventions for agent runtime observability. They are candidates for upstream OTel SemConv proposals.

- `gen_ai.agent_ext.turn_id` — conversation turn identifier
- `gen_ai.agent_ext.component_type` — see above
- `gen_ai.agent_ext.retry_count` — retry attempts
- `gen_ai.agent_ext.latency_ms` — explicit latency in milliseconds
- `gen_ai.agent_ext.module` — source module name
- `gen_ai.agent_ext.module_kind` — module category
- `gen_ai.agent_ext.semantic_kind` — semantic operation kind
- `gen_ai.agent_ext.verify_id` — pipeline verification canary id

## Memory / knowledge monitoring fields

- `gen_ai.agent_ext.retrieval_latency_ms` — retrieval round-trip time
- `gen_ai.agent_ext.cache_hit` — whether the retrieval hit a cache
- `gen_ai.agent_ext.retrieval_score` — similarity / relevance score
- `gen_ai.agent_ext.knowledge_source` — knowledge base identifier

## Guardrail / safety fields

- `gen_ai.guardrail.action` — `pass` / `block` / `redact`
- `gen_ai.guardrail.rule_id` — which guardrail rule fired
- `gen_ai.guardrail.category` — `content_safety` / `prompt_injection` / `pii` / `custom`
- `gen_ai.guardrail.latency_ms` — guardrail check latency

## Evaluation observability fields

- `gen_ai.evaluation.run_id` — evaluation run identifier
- `gen_ai.evaluation.evaluator` — evaluator name
- `gen_ai.evaluation.score` — numeric score
- `gen_ai.evaluation.outcome` — `pass` / `fail` / `degraded`
- `gen_ai.evaluation.dimension` — `quality` / `safety` / `latency` / `efficiency`
- `gen_ai.evaluation.name` — OTel-compatible evaluator / metric name

This repo keeps `gen_ai.evaluation.score` as a numeric leaf for backward compatibility. Elasticsearch cannot map both `gen_ai.evaluation.score` and `gen_ai.evaluation.score.value` in the same index, so `score.value` / `score.label` are not emitted by default.

## Reasoning trace fields

Record **why** the agent chose a particular action at each decision point. These fields turn flat event logs into an explainable decision trail.

- `gen_ai.agent_ext.reasoning.action` — chosen action: `tool_call` / `delegate` / `respond` / `wait` / `escalate`
- `gen_ai.agent_ext.reasoning.alternatives` — rejected alternatives (comma-separated)
- `gen_ai.agent_ext.reasoning.rationale` — free-text why-this-action explanation (NOT the raw prompt)
- `gen_ai.agent_ext.reasoning.confidence` — agent's self-reported confidence 0–1
- `gen_ai.agent_ext.reasoning.input_summary` — condensed input context
- `gen_ai.agent_ext.reasoning.decision_type` — `routing` / `tool_selection` / `delegation` / `termination` / `retry`
- `gen_ai.agent_ext.reasoning.step_index` — ordinal within the turn (0-based)

## User feedback fields

Collect end-user feedback and link it to traces/sessions. The bridge exposes `POST /v1/feedback` to accept feedback; the generated instrumentation snippet does NOT handle this — it's the product team's responsibility to wire their UI.

- `gen_ai.feedback.score` — numeric score (e.g. 1–5, or -1/0/1 for thumbs up/down)
- `gen_ai.feedback.sentiment` — `positive` / `negative` / `neutral` (auto-derived from score if omitted)
- `gen_ai.feedback.comment` — free-text user comment
- `gen_ai.feedback.trace_id` — links feedback to a specific `trace.id`
- `gen_ai.feedback.session_id` — links feedback to a `gen_ai.conversation.id`
- `gen_ai.feedback.user_id` — opaque end-user identifier

## Sensitive content policy

Default: do not store raw prompts, completions, chat messages, system instructions, tool definitions, tool arguments, or tool results.

The bridge and ingest pipeline remove these fields by default:

- `gen_ai.prompt`
- `gen_ai.completion`
- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.system_instructions`
- `gen_ai.tool.definitions`
- `gen_ai.tool.call.arguments`
- `gen_ai.tool.call.result`

If a team needs payload capture, add a separate opt-in path with truncation, PII filtering, and retention rules. Do not enable it silently.

## Important rule

Do **not** rely on flat legacy fields such as `agent_id`, `tool_name`, `token_input`, or `captured_at`.
Do **not** use pre-v2 field names like `gen_ai.agent.tool_name` or `gen_ai.agent.session_id` — they have been replaced by OTel standard equivalents.

## Mapping boundary rule

The generated Elasticsearch component template is now **root-dynamic = false**.
If a structured `message` JSON payload contains unknown **top-level** objects, the ingest pipeline routes them into `labels.unmapped` instead of creating new root mappings.
Known ECS / OTel fields should still be emitted explicitly as canonical fields rather than hidden inside `message`.

## Time field

The canonical and default reporting time field is `@timestamp`.
