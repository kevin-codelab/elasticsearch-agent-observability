# Telemetry Schema

## Minimum shared fields (legacy → ECS)

The repo accepts these legacy field names on ingestion and automatically renames them to ECS via the ingest pipeline:

| Legacy field | ECS field |
|---|---|
| `agent_id` | `agent.id` |
| `run_id` | `gen_ai.agent.run_id` |
| `turn_id` | `gen_ai.agent.turn_id` |
| `span_id` | `span.id` |
| `parent_span_id` | `parent.id` |
| `signal_type` | `gen_ai.agent.signal_type` |
| `semantic_kind` | `gen_ai.agent.semantic_kind` |
| `tool_name` | `gen_ai.agent.tool_name` |
| `model_name` | `gen_ai.agent.model_name` |
| `latency_ms` | `gen_ai.agent.latency_ms` (also computes `event.duration` in ns) |
| `token_input` | `gen_ai.usage.input_tokens` |
| `token_output` | `gen_ai.usage.output_tokens` |
| `cost` | `gen_ai.agent.cost` |
| `error_type` | `gen_ai.agent.error_type` |
| `retry_count` | `gen_ai.agent.retry_count` |
| `mcp_method_name` | `gen_ai.agent.mcp_method_name` |
| `session_id` | `gen_ai.agent.session_id` |
| `captured_at` | alias → `@timestamp` |

ECS fields (`@timestamp`, `event.outcome`, `service.name`, etc.) are also accepted directly and won't be renamed.

## Time field

The canonical time field is `@timestamp`. `captured_at` is kept as an alias for backward compatibility.
