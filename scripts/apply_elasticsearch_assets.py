#!/usr/bin/env python3
"""Apply generated Elasticsearch observability assets to a cluster."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from common import ESConfig, SkillError, build_events_alias, es_request, print_error, read_json, validate_credential_pair, validate_index_prefix, validate_workspace_dir


RESOURCE_ALREADY_EXISTS = "resource_already_exists_exception"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply generated Elasticsearch observability assets")
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--es-user", default="")
    parser.add_argument("--es-password", default="")
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--skip-bootstrap-index", action="store_true", help="Skip creating the initial write index and alias")
    return parser.parse_args()


def load_assets(assets_dir: Path) -> dict[str, Any]:
    resolved = validate_workspace_dir(assets_dir, "Assets directory")
    return {
        "index_template": read_json(resolved / "index-template.json"),
        "ingest_pipeline": read_json(resolved / "ingest-pipeline.json"),
        "ilm_policy": read_json(resolved / "ilm-policy.json"),
        "report_config": read_json(resolved / "report-config.json"),
    }


def ensure_write_index(config: ESConfig, index_prefix: str) -> dict[str, str]:
    events_alias = build_events_alias(index_prefix)
    write_index = f"{events_alias}-000001"
    create_payload = {"aliases": {events_alias: {"is_write_index": True}}}
    status = "created"
    try:
        es_request(config, "PUT", f"/{write_index}", create_payload)
    except SkillError as exc:
        if RESOURCE_ALREADY_EXISTS not in str(exc):
            raise
        status = "already_exists"
        es_request(
            config,
            "POST",
            "/_aliases",
            {"actions": [{"add": {"index": write_index, "alias": events_alias, "is_write_index": True}}]},
        )
    return {"events_alias": events_alias, "write_index": write_index, "status": status}


def apply_assets(config: ESConfig, *, assets_dir: Path, index_prefix: str, bootstrap_index: bool = True) -> dict[str, Any]:
    validated_prefix = validate_index_prefix(index_prefix)
    assets = load_assets(assets_dir)
    template_name = f"{validated_prefix}-events-template"
    pipeline_name = f"{validated_prefix}-normalize"
    ilm_name = f"{validated_prefix}-lifecycle"

    responses = {
        "ilm_policy": es_request(config, "PUT", f"/_ilm/policy/{ilm_name}", assets["ilm_policy"]),
        "ingest_pipeline": es_request(config, "PUT", f"/_ingest/pipeline/{pipeline_name}", assets["ingest_pipeline"]),
        "index_template": es_request(config, "PUT", f"/_index_template/{template_name}", assets["index_template"]),
        "report_config": assets["report_config"],
    }
    bootstrap_summary = None
    if bootstrap_index:
        bootstrap_summary = ensure_write_index(config, validated_prefix)
    return {
        "assets_dir": str(assets_dir),
        "index_prefix": validated_prefix,
        "template_name": template_name,
        "pipeline_name": pipeline_name,
        "ilm_policy_name": ilm_name,
        "events_alias": build_events_alias(validated_prefix),
        "bootstrap_index": bootstrap_summary,
        "responses": responses,
    }


def main() -> int:
    try:
        args = parse_args()
        credentials = validate_credential_pair(args.es_user, args.es_password)
        config = ESConfig(
            es_url=args.es_url,
            es_user=credentials[0] if credentials else None,
            es_password=credentials[1] if credentials else None,
        )
        summary = apply_assets(
            config,
            assets_dir=Path(args.assets_dir).expanduser().resolve(),
            index_prefix=args.index_prefix,
            bootstrap_index=not args.skip_bootstrap_index,
        )
        print("✅ Elasticsearch assets applied")
        print(f"   alias: {summary['events_alias']}")
        if summary["bootstrap_index"]:
            print(f"   write index: {summary['bootstrap_index']['write_index']}")
            print(f"   bootstrap status: {summary['bootstrap_index']['status']}")
        return 0
    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to apply Elasticsearch assets: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
