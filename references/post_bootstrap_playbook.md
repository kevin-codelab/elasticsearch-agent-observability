# Post-Bootstrap Playbook

Bootstrap is over; something (you, an AI agent, or CI) just ran `bootstrap_observability.py` and got a `generated/` directory plus ES/Kibana assets.

If ingestion is healthy, Tier 1 should show latency, error rate, and token totals. Panels that depend on Tier 2 fields may stay empty until the agent emits those fields. Treat that as setup work, not missing data to fake.

This playbook lists the next setup steps in order.

## Level 0 — Confirm the pipeline actually ingests (do this first)

Before filling any panel, confirm data really reaches Elasticsearch. This is where most "Collector is up but ES is empty" failures hide.

Bootstrap now runs this automatically when `--apply-es-assets` is on; the result is written to `verify.json` next to the other artifacts. If you skipped it or need to re-run:

```bash
python scripts/verify_pipeline.py \
  --es-url <url> --es-user <user> --es-password <pass> \
  --index-prefix <prefix> \
  --otlp-http-endpoint http://127.0.0.1:14319   # bridge by default, or 4318 for Collector OTLP HTTP
```

Exit code contract:

- `0` canary was sent and indexed — you can move on to Level 1.
- `2` sent but lost, or indexed with the wrong shape — read the `next_step` field in the JSON output and apply it. Most common resolution: switch `--otlp-http-endpoint` from the Collector to the bridge (`:14319`) to unblock ingestion, then fix the Collector ES exporter separately.
- `1` could not send or could not reach ES at all — nothing downstream will work until the transport or credentials are fixed; do not continue.

Recommended first-install posture: **point the agent at the OTLP HTTP bridge first** (`http://127.0.0.1:14319`). It is a narrower path and uses the same data stream / dashboards. Move to the native Collector ES exporter once the bridge path is stable.

## Level 1 — Tier 2 business fields (biggest ROI)

Goal: fill the empty tool/model/session/turn panels.

- [ ] Wrap every **tool call** site with `tracedToolCall("<tool_name>", ...)` or manual span + `gen_ai.tool.name`.
- [ ] Wrap every **model call** site with `tracedModelCall("<model_name>", ...)` or set `gen_ai.request.model` + token fields.
- [ ] At the **session boundary** (inbound request / conversation starter), open a span with `gen_ai.conversation.id`. All child spans inherit it via OTel context.
- [ ] At each **conversation turn** boundary, open a child span with `gen_ai.agent_ext.turn_id`.
- [ ] Tag every span with `gen_ai.agent_ext.component_type` (`tool` / `llm` / `mcp` / `memory` / `knowledge` / `guardrail` / `runtime`).

Verify: after traffic, the dashboard's "tool mix", "model mix", "sessions", and "slow turns" panels should have data.

## Level 2 — Sharper error/retry signals

Goal: stop getting generic "HTTP 500" alerts; start seeing "timeout concentrated in tool X".

- [ ] Classify exceptions into `error.type`. Suggested values: `timeout` / `rate_limit` / `api_error` / `auth_error` / `tool_error` / `validation_error` / `unknown`.
- [ ] At the retry point, set `gen_ai.agent_ext.retry_count` to the running count (not a boolean).
- [ ] If the agent has a native latency measurement already, also set `gen_ai.agent_ext.latency_ms` (the alert uses it for long-turn detection independent of span duration).

Verify: `alert_and_diagnose.py --time-range now-15m` can cite specific `error_type` / tool / retry counts when those fields exist in recent data.

## Level 3 — Reasoning, evaluation, feedback, and guardrails

Goal: fill the reasoning/eval/feedback/guardrail panels. Only do these when Levels 1 and 2 are solid.

These fields **cannot be auto-injected** — they require active reporting from the agent or an external system.

### Reasoning trace (`gen_ai.agent_ext.reasoning.*`)

**When to emit**: at every agent decision point — when the agent chooses an action (tool call, delegate, respond, escalate).

- [ ] `gen_ai.agent_ext.reasoning.action` — what the agent decided to do: `tool_call` / `delegate` / `respond` / `wait` / `escalate`
- [ ] `gen_ai.agent_ext.reasoning.decision_type` — category: `routing` / `tool_selection` / `delegation` / `termination` / `retry`
- [ ] `gen_ai.agent_ext.reasoning.rationale` — free-text why (truncated to 500 chars by ingest pipeline)
- [ ] `gen_ai.agent_ext.reasoning.alternatives` — rejected alternatives (comma-separated)
- [ ] `gen_ai.agent_ext.reasoning.confidence` — 0-1 self-reported confidence

**How**: use `traced_decision()` decorator or `emit_reasoning_span()` from `instrument_frameworks.py`. If no OTel SDK is available, include these fields as attributes in your OTLP log payload to the bridge.

### Evaluation (`gen_ai.evaluation.*`)

**When to emit**: after an evaluation run scores the agent's responses.

- [ ] `gen_ai.evaluation.outcome` — `pass` / `fail` / `degraded`
- [ ] `gen_ai.evaluation.dimension` — `quality` / `safety` / `latency`
- [ ] `gen_ai.evaluation.score` — numeric score from the evaluator
- [ ] `gen_ai.evaluation.evaluator` — which evaluator produced this (e.g. `llm_judge`, `latency_check`)

**How**: run `evaluate.py run --es-url <url> --write-to-es`. The evaluators write project fields plus `gen_ai.evaluation.name`. They do not emit `gen_ai.evaluation.score.value` because it conflicts with the existing `gen_ai.evaluation.score` mapping in Elasticsearch.

### User feedback (`gen_ai.feedback.*`)

**When to emit**: after the end user rates or comments on a response.

- [ ] `gen_ai.feedback.score` — numeric (e.g. 1-5, or -1/0/1 for thumbs)
- [ ] `gen_ai.feedback.sentiment` — `positive` / `negative` / `neutral` (auto-derived from score if omitted)
- [ ] `gen_ai.feedback.comment` — free-text (truncated to 1000 chars)

**How**: `POST /v1/feedback` on the bridge. Simple JSON, no OTLP wrapping needed:
```json
{"score": 5, "sentiment": "positive", "comment": "Good answer", "session_id": "sess-001"}
```

### Guardrail (`gen_ai.guardrail.*`)

**When to emit**: when a safety filter runs on input or output.

- [ ] `gen_ai.guardrail.action` — `pass` / `block` / `redact`
- [ ] `gen_ai.guardrail.category` — `content_safety` / `prompt_injection` / `pii` / `custom`
- [ ] `gen_ai.guardrail.latency_ms` — filter execution time

**How**: include these fields as span attributes if the agent has safety filters, or POST to the bridge.

- [ ] Production alerts — start from `generated/alert-rule-specs.json` and create Kibana Query Rules through the UI/API.
- [ ] Investigations — start from `generated/investigation-queries.json`; keep complex questions in ES|QL/Discover instead of adding more dashboard panels.
- [ ] Custom Kibana panels — add them via `--dashboard-extensions` on a follow-up bootstrap; don't hand-edit the generated saved objects.

## What to do with the `generated/` directory

`generated/` is output, not source. Treat it like a build artifact:

- **Do** review it before applying to production.
- **Do** add it to `.gitignore` in your own workspace.
- **Don't** hand-edit the files and expect the changes to survive the next bootstrap — re-render instead with the right flags.
- **Don't** check ES credentials in (see [`credentials_playbook.md`](credentials_playbook.md)).

## When NOT to keep extending

Stop when:

- The dashboard answers the operational questions your team actually asks.
- Alerts are actionable often enough to keep them enabled.
- Slow or failed sessions can be traced to a service, tool, model, or error type.

More fields do not automatically mean more value. The dashboard is a fixed surface; fields that no panel consumes are just bytes in ES.

## Propagating changes back upstream

If you added a new span type that deserves its own dashboard panel, update in one PR:

1. `references/instrumentation_contract.md` — list the new field and what it powers.
2. `references/telemetry_schema.md` — add to the field dictionary.
3. `scripts/render_es_assets.py` — add the Kibana saved object.
4. `scripts/alert_and_diagnose.py` — if the field should drive a rule.

Skipping step 1 is the single most common way to re-introduce "ghost fields" (fields that exist in data but no panel knows about).
