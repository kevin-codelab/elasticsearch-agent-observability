# Config Guide

## Target environments

This repo is designed for:

- self-hosted Elasticsearch 9.x
- Tencent Cloud Elasticsearch Service 9.x

## Main outputs

The bootstrap flow now covers both generated artifacts and first-step apply outputs:

- Collector config
- Collector launcher script
- agent OTLP env template
- Elasticsearch index template
- ingest pipeline
- ILM policy
- report config
- apply summary
- first report output

## Default assumptions

- OTLP is the main ingestion path
- Elasticsearch is the main storage and query backend
- prompts and tool payloads should be redacted or summarized by default
- generated assets should stay reviewable JSON / YAML / shell files, not hidden runtime state

## Minimal bootstrap

```bash
python scripts/bootstrap_observability.py \
  --workspace /path/to/workspace \
  --output-dir generated/bootstrap \
  --es-url http://localhost:9200 \
  --apply-es-assets
```

## What this gives you

At minimum, the command above should leave you with:

- generated Collector config
- generated Elasticsearch assets
- applied template / pipeline / ILM policy
- bootstrapped first write index alias
- generated report output

## Rule

Prefer generated config and assets over hand-written setup notes.
That keeps the repo deterministic and easier to publish.
