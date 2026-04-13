# Telemetry Schema

## Primary contract

This repo now targets a **9.x ECS / GenAI-native ingest contract**.

Send canonical fields directly:

- `@timestamp`
- `event.*`
- `service.*`
- `agent.*`
- `trace.id`
- `span.id`
- `parent.id`
- `transaction.id`
- `gen_ai.agent.*`
- `gen_ai.usage.*`
- `gen_ai.guardrail.*`
- `gen_ai.evaluation.*`

## Component type tagging

Use `gen_ai.agent.component_type` to tag spans with their component category:

- `runtime` — agent runtime entrypoint
- `llm` — model inference call
- `tool` — tool execution
- `mcp` — MCP protocol call
- `memory` — memory store read/write
- `knowledge` — knowledge base / RAG retrieval
- `guardrail` — safety check / content filter

This enables per-component monitoring in Kibana (similar to AgentKit's per-component dashboards).

## Memory / knowledge monitoring fields

- `gen_ai.agent.retrieval_latency_ms` — retrieval round-trip time
- `gen_ai.agent.cache_hit` — whether the retrieval hit a cache
- `gen_ai.agent.retrieval_score` — similarity / relevance score
- `gen_ai.agent.knowledge_source` — knowledge base identifier

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
- `gen_ai.evaluation.dimension` — `quality` / `safety` / `latency` / `cost`

## Important rule

Do **not** rely on flat legacy fields such as `agent_id`, `tool_name`, `token_input`, or `captured_at`.
The generated ingest pipeline no longer remaps those fields for you.

## Time field

The canonical and default reporting time field is `@timestamp`.
