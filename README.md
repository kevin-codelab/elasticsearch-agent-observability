# elasticsearch-agent-observability

A skill that gives an AI agent a black box in Elasticsearch and Kibana.
It turns traces, tool calls, token usage, failures, and latency into a working observability surface instead of a pile of ad hoc scripts.

## A story

You already have an agent in production.
It calls models, tools, and maybe MCP endpoints.
At first it looks fine. A few days later, the same pattern shows up every time:

- users say the agent is sometimes slow
- token cost keeps going up
- some runs fail at random
- nobody can tell whether the problem is in the model call, the tool call, the MCP layer, or the telemetry pipeline

The hard part is not opening Kibana.
The hard part is building everything that Kibana needs before it becomes useful:

- OpenTelemetry Collector configuration
- Elasticsearch data streams and mappings
- ingest pipelines and lifecycle policies
- Kibana data views, searches, and dashboards
- alert diagnosis and drift checks

This skill does that setup work.
It inspects the workspace, generates the Elasticsearch-side assets, and gives the agent a ready-to-use observability starter on top of Elasticsearch and Kibana.

## What the skill does

- **Inspect the workspace**: detect runtime modules, model adapters, tool registries, and MCP surfaces
- **Generate the collection layer**: output OpenTelemetry Collector configuration, environment files, and launch scripts
- **Generate Elasticsearch assets**: output data streams, index templates, ingest pipelines, and lifecycle policies
- **Generate Kibana assets**: output data views, saved searches, Lens visualizations, and an overview dashboard
- **Diagnose failures**: detect error-rate spikes, latency regressions, and token anomalies, then produce RCA output
- **Check drift**: compare the live Elasticsearch cluster with locally generated assets
- **Archive conclusions**: write RCA results into `elasticsearch-insight-store`
- **Support multiple ingest modes**: `collector`, `elastic-agent-fleet`, and `apm-otlp-hybrid`

## Why an agent would use it

An agent should not hand-build observability plumbing every time.
This skill gives the agent one place to bootstrap, validate, and diagnose an Elasticsearch-based observability setup.

Use it when the request sounds like:

- "add observability to this agent"
- "set up OpenTelemetry, Elasticsearch, and Kibana for this workspace"
- "generate the Collector, Elasticsearch, and Kibana assets"
- "check whether the observability setup drifted from the cluster"
- "diagnose recent agent failures and store the conclusion"

## Skill contract

Treat this skill as an Elasticsearch observability bootstrapper.

- **`bootstrap`**: inspect the workspace and run the discovery → render → dry-run/apply flow
- **`diagnose`**: run `alert_and_diagnose.py` and return RCA output
- **`validate`**: run `validate_state.py` and compare generated assets with the live Elasticsearch cluster

## Common commands

### Bootstrap the observability stack

```bash
python scripts/bootstrap_observability.py \
  --workspace <workspace> \
  --es-url <elasticsearch-url> \
  --apply-es-assets \
  --apply-kibana-assets
```

### Diagnose recent issues

```bash
python scripts/alert_and_diagnose.py \
  --es-url <elasticsearch-url> \
  --index-prefix <index-prefix>
```

### Store RCA results in the insight store

```bash
python scripts/alert_and_diagnose.py \
  --es-url <elasticsearch-url> \
  --index-prefix <index-prefix> \
  --store-to-insight <path-to-store.py>
```

### Validate cluster drift

```bash
python scripts/validate_state.py \
  --es-url <elasticsearch-url> \
  --generated-dir <generated-dir>
```

## Generated output

```text
generated/bootstrap/
├── discovery.json
├── otel-collector.generated.yaml
├── run-collector.sh
├── agent-otel.env
├── agent_otel_bootstrap.py
├── elastic-native/
├── elasticsearch/
│   ├── component-template-*.json
│   ├── index-template.json
│   ├── ingest-pipeline.json
│   ├── ilm-policy.json
│   ├── kibana-saved-objects.json
│   └── apply-summary.json
└── bootstrap-summary.md
```

## Extension points

- **Dashboard extensions**: inject extra panels with `--dashboard-extensions`
- **Knowledge loop**: archive RCA output into `elasticsearch-insight-store`
- **Safe defaults**: keep credentials in environment variables and redact sensitive generative AI fields in the ingest pipeline

## Requirements

- Elasticsearch 9.x
- Kibana
- `otelcol-contrib` with `spanmetrics` and the Elasticsearch exporter
- Basic license is enough
