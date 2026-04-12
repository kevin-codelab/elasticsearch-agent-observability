# elasticsearch-agent-observability

> Bootstrap the Elastic side of agent observability: Collector config, Elasticsearch assets, Kibana entry surface, and a small reporting / alert toolchain.

## What This Repo Is

This repo is an **observability bootstrap tool** for agents.

It is useful when you already know you want:

- OpenTelemetry for collection
- Elasticsearch for storage
- Kibana as the main human-facing surface

It is **not** a full observability product.
It does **not** auto-rewrite arbitrary runtimes.
It does **not** make runtime instrumentation disappear.

A more honest description is:

**discover → render → dry-run/apply → wire runtime → observe**

## Who This Is For

Best fit:

- platform engineers
- SRE / observability engineers
- agent infra owners already running Elastic / Kibana

Weak fit:

- application developers expecting one command to auto-instrument any agent
- teams that do not want to touch Collector / Fleet / runtime wiring

## What It Does Well

- inspect a workspace and discover monitorable modules
- recommend an ingest mode: `collector`, `elastic-agent-fleet`, or `apm-otlp-hybrid`
- render OTel Collector config with traces, logs, metrics, `spanmetrics`, optional `filelog`, and sampling
- render an Elastic-native starter bundle for Fleet / APM operators
- render Elasticsearch assets with data streams, ECS-compatible mappings, component templates, ingest pipeline, and tiered ILM
- render Kibana saved objects: data view, searches, Lens visualizations, and a starter dashboard
- apply ES / Kibana assets, or preview the full plan with `--dry-run`
- generate a Python instrumentation starter file
- generate a smoke report from the same report contract
- run standalone alert checks with rule-based root-cause hints via `alert_and_diagnose.py`

## Recommended Flow

### 1) Render first

```bash
python scripts/bootstrap_observability.py \
  --workspace /path/to/your-agent \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --kibana-url http://localhost:5601 \
  --generate-instrument-snippet
```

### 2) Preview what would be applied

```bash
python scripts/bootstrap_observability.py \
  --workspace /path/to/your-agent \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --apply-es-assets \
  --apply-kibana-assets \
  --dry-run
```

### 3) Apply for real

```bash
python scripts/bootstrap_observability.py \
  --workspace /path/to/your-agent \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --apply-es-assets \
  --kibana-url http://localhost:5601 \
  --apply-kibana-assets \
  --generate-instrument-snippet
```

## Prerequisites That Actually Matter

- target stack: **self-hosted Elasticsearch 9.x** or **Tencent Cloud Elasticsearch Service 9.x**
- Kibana must accept standard saved-object APIs if you want saved objects applied
- the generated Collector config uses contrib-only components such as `spanmetrics` and the Elasticsearch exporter
- the launcher therefore defaults to **`otelcol-contrib`**, or another custom Collector distribution that includes equivalent components
- the Python snippet still requires runtime dependencies such as `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-grpc`

If you skip this section, the most common failure mode is: **render succeeds, Collector runtime does not**.

## Ingest Modes

```bash
# Default: Collector-only
--ingest-mode collector

# Elastic Agent + Fleet managed enrollment
--ingest-mode elastic-agent-fleet \
  --fleet-server-url https://fleet.example.com:8220 \
  --fleet-enrollment-token <token>

# Hybrid: Collector for OTLP + Elastic-native for APM/Fleet
--ingest-mode apm-otlp-hybrid \
  --apm-server-url https://apm.example.com:8200
```

## What You Get

```text
generated/bootstrap/
├── discovery.json
├── otel-collector.generated.yaml
├── run-collector.sh
├── agent-otel.env
├── agent_otel_bootstrap.py          ← only with --generate-instrument-snippet
├── report.md                        ← only after real apply / explicit report output
├── elastic-native/                  ← only with fleet/hybrid mode
│   ├── elastic-agent-policy.json
│   ├── elastic-agent.env
│   ├── run-elastic-agent.sh
│   └── README.md
├── elasticsearch/
│   ├── component-template-ecs-base.json
│   ├── component-template-settings.json
│   ├── index-template.json
│   ├── ingest-pipeline.json
│   ├── ilm-policy.json
│   ├── report-config.json
│   ├── kibana-saved-objects.json
│   ├── kibana-saved-objects.ndjson
│   ├── apply-summary.json           ← only when apply / dry-run is requested
│   └── sanity-check.json            ← only after a real apply
└── bootstrap-summary.md
```

## Dry-Run vs Real Apply

`--dry-run` means:

- assets are still rendered to disk
- an apply plan is written to `apply-summary.json`
- **no** Elasticsearch request is sent
- **no** Kibana request is sent
- **no** sanity check is executed
- **no** smoke report query is executed

Real apply means:

- ES assets are pushed
- Kibana saved objects are pushed when requested
- a sanity check writes / reads / deletes a test document
- a smoke report can be generated from the same contract

## Storage Model

- **data streams** instead of legacy rollover aliases
- **component templates**: `{prefix}-ecs-base` + `{prefix}-settings`
- **ECS-compatible field names**: `@timestamp`, `event.outcome`, `service.name`, `trace.id`, `gen_ai.usage.*`, `gen_ai.agent.*`
- **backward compatibility**: legacy fields such as `agent_id`, `tool_name`, and `latency_ms` are migrated in the ingest pipeline
- **GenAI SemConv preservation**: `gen_ai.usage.*`, `gen_ai.request.model`, and related fields are preserved instead of being dropped

## Kibana Surface

The generated Kibana bundle includes:

| Object | Type | Description |
|---|---|---|
| Data view | index-pattern | `{prefix}-events*`, time field `@timestamp` |
| Event stream | search | Full event stream in Discover |
| Failure stream | search | `event.outcome:failure` or ingest-error events |
| Event rate chart | lens (XY) | Event count over time, split by outcome |
| Latency P50/P95 | lens (metric) | Percentiles from `event.duration` |
| Top tools | lens (pie) | Most-called agent tools |
| Token usage | lens (XY) | Input vs output token trend |
| Overview dashboard | dashboard | One starter surface for all of the above |

## Alerting

`alert_and_diagnose.py` is a **standalone script**, not a Kibana alerting replacement.

```bash
python scripts/alert_and_diagnose.py \
  --es-url http://localhost:9200 \
  --index-prefix agent-obsv \
  --time-range now-15m
```

Current checks:

- error-rate spike
- token consumption anomaly
- latency degradation

The RCA output is rule-based and practical, but it is **not** a full diagnosis engine.

## Current Boundaries

- this repo does **not** rewrite the agent SDK or runtime code
- the Python snippet is a starter file; you still need to import or wire it into the actual entrypoint
- the Elastic-native bundle is render-only; it does not call Fleet APIs for you
- normalization handles ECS mapping, legacy field migration, and JSON body parsing, but it is not a universal schema parser
- tests mostly prove renderer / contract behavior, not full runtime → Collector → ES → Kibana end-to-end readiness
- all generated assets are designed for the **Basic** Elasticsearch license; no paid Kibana alerting features are required

## Security Defaults

- credentials stay in env placeholders unless `--embed-es-credentials` is used
- sensitive GenAI payloads such as prompts, completions, tool arguments, and tool results are redacted in the ingest pipeline
- generated files stay readable JSON / YAML / Python instead of hidden state

## Repo Layout

```text
SKILL.md      Trigger and execution contract
scripts/      Discovery, rendering, apply, instrumentation, reporting
references/   Config, reporting, and architecture notes
generated/    Example output bundles
```
