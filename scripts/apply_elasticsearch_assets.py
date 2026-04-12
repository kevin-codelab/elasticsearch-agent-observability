#!/usr/bin/env python3
"""Apply generated Elasticsearch observability assets to a cluster."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from common import (
    ESConfig,
    SkillError,
    build_component_template_name,
    build_data_stream_name,
    build_events_alias,
    es_request,
    print_error,
    read_json,
    validate_credential_pair,
    validate_index_prefix,
    validate_workspace_dir,
)

RESOURCE_ALREADY_EXISTS = "resource_already_exists_exception"


def sanity_check(config: ESConfig, *, index_prefix: str) -> dict[str, Any]:
    """Write a test document, refresh, query, and delete it to verify the pipeline is working end-to-end."""
    import time
    ds_name = build_data_stream_name(index_prefix)
    test_doc = {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "event.action": "_sanity_check",
        "event.kind": "event",
        "event.outcome": "success",
        "service.name": "sanity-check",
        "gen_ai.agent.tool_name": "_sanity_check_tool",
        "gen_ai.agent.signal_type": "sanity_check",
        "message": "End-to-end sanity check document",
    }
    try:
        index_result = es_request(config, "POST", f"/{ds_name}/_doc", test_doc)
        doc_id = index_result.get("_id", "")
        if not doc_id:
            return {"status": "failed", "reason": "Index returned no _id", "detail": index_result}
        es_request(config, "POST", f"/{ds_name}/_refresh")
        time.sleep(0.5)
        query = {"query": {"term": {"event.action": "_sanity_check"}}, "size": 1}
        search_result = es_request(config, "POST", f"/{ds_name}/_search", query)
        hits = search_result.get("hits", {}).get("total", {}).get("value", 0)
        if hits < 1:
            return {"status": "failed", "reason": "Sanity check doc not found after indexing", "doc_id": doc_id}
        found_doc = search_result["hits"]["hits"][0]["_source"]
        pipeline_applied = found_doc.get("observer.product") == "elasticsearch-agent-observability"
        es_request(config, "POST", f"/{ds_name}/_delete_by_query", {"query": {"term": {"event.action": "_sanity_check"}}})
        return {
            "status": "passed",
            "doc_id": doc_id,
            "pipeline_applied": pipeline_applied,
            "indexed_fields_sample": list(found_doc.keys())[:10],
        }
    except SkillError as exc:
        return {"status": "failed", "reason": str(exc)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply generated Elasticsearch observability assets")
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--es-user", default="")
    parser.add_argument("--es-password", default="")
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--skip-bootstrap-index", action="store_true", help="Skip creating the data stream")
    parser.add_argument("--kibana-url", default="", help="Optional Kibana base URL for applying saved objects")
    parser.add_argument("--kibana-space", default="default")
    parser.add_argument("--skip-kibana-assets", action="store_true", help="Skip applying Kibana saved objects even if present")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be applied without actually sending requests")
    return parser.parse_args()


def load_assets(assets_dir: Path) -> dict[str, Any]:
    resolved = validate_workspace_dir(assets_dir, "Assets directory")
    kibana_json = resolved / "kibana-saved-objects.json"
    result: dict[str, Any] = {
        "index_template": read_json(resolved / "index-template.json"),
        "ingest_pipeline": read_json(resolved / "ingest-pipeline.json"),
        "ilm_policy": read_json(resolved / "ilm-policy.json"),
        "report_config": read_json(resolved / "report-config.json"),
        "kibana_saved_objects": read_json(kibana_json) if kibana_json.exists() else None,
    }
    ecs_base_path = resolved / "component-template-ecs-base.json"
    settings_path = resolved / "component-template-settings.json"
    if ecs_base_path.exists():
        result["component_template_ecs_base"] = read_json(ecs_base_path)
    if settings_path.exists():
        result["component_template_settings"] = read_json(settings_path)
    return result


def kibana_request(config: ESConfig, kibana_url: str, method: str, path: str, payload: dict | None = None, *, body_bytes: bytes | None = None) -> dict[str, Any]:
    url = kibana_url.rstrip("/") + path
    request = urllib.request.Request(url, method=method.upper())
    request.add_header("Content-Type", "application/json")
    request.add_header("kbn-xsrf", "true")
    if config.kibana_api_key:
        request.add_header("Authorization", f"ApiKey {config.kibana_api_key}")
    elif config.es_user and config.es_password:
        token = base64.b64encode(f"{config.es_user}:{config.es_password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    body = body_bytes
    if body is None and payload is not None:
        body = json.dumps(payload).encode("utf-8")
    import ssl
    context = None
    if not config.verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, data=body, timeout=config.timeout_seconds, context=context) as response:  # noqa: S310
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise SkillError(f"Kibana HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SkillError(f"Unable to reach Kibana: {exc.reason}") from exc
    if not text:
        return {"acknowledged": True}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid JSON response from Kibana: {text[:200]}") from exc


def build_space_prefix(space: str) -> str:
    normalized = space.strip() or "default"
    return "" if normalized == "default" else f"/s/{quote(normalized, safe='')}"


def ensure_data_stream(config: ESConfig, index_prefix: str) -> dict[str, str]:
    ds_name = build_data_stream_name(index_prefix)
    status = "created"
    try:
        es_request(config, "PUT", f"/_data_stream/{ds_name}")
    except SkillError as exc:
        if RESOURCE_ALREADY_EXISTS in str(exc) or "already exists" in str(exc).lower():
            status = "already_exists"
        else:
            raise
    return {"data_stream": ds_name, "status": status}


def ensure_write_index(config: ESConfig, index_prefix: str) -> dict[str, str]:
    """Backward compat: create data stream, or fall back to legacy alias bootstrap."""
    try:
        return ensure_data_stream(config, index_prefix)
    except SkillError:
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


def apply_kibana_saved_objects(config: ESConfig, *, kibana_url: str, kibana_space: str, bundle: dict[str, Any]) -> dict[str, Any]:
    objects = bundle.get("objects", []) if isinstance(bundle, dict) else []
    if not objects:
        return {"status": "skipped", "count": 0, "objects": []}
    space_prefix = build_space_prefix(kibana_space)
    applied: list[dict[str, str]] = []
    for saved_object in objects:
        object_type = str(saved_object.get("type", "")).strip()
        object_id = str(saved_object.get("id", "")).strip()
        if not object_type or not object_id:
            raise SkillError("Each Kibana saved object must include type and id")
        payload = {
            "attributes": saved_object.get("attributes", {}),
            "references": saved_object.get("references", []),
        }
        path = f"{space_prefix}/api/saved_objects/{quote(object_type, safe='')}/{quote(object_id, safe='')}?overwrite=true"
        response = kibana_request(config, kibana_url, "POST", path, payload)
        applied.append(
            {
                "type": object_type,
                "id": object_id,
                "title": str(saved_object.get("attributes", {}).get("title", object_id)),
                "response_id": str(response.get("id", object_id)),
            }
        )
    return {
        "status": "applied",
        "space": kibana_space,
        "count": len(applied),
        "objects": applied,
    }


def apply_assets(
    config: ESConfig,
    *,
    assets_dir: Path,
    index_prefix: str,
    bootstrap_index: bool = True,
    kibana_url: str | None = None,
    kibana_space: str = "default",
    apply_kibana: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    validated_prefix = validate_index_prefix(index_prefix)
    assets = load_assets(assets_dir)
    template_name = f"{validated_prefix}-events-template"
    pipeline_name = f"{validated_prefix}-normalize"
    ilm_name = f"{validated_prefix}-lifecycle"

    if dry_run:
        plan: list[dict[str, str]] = [
            {"action": "PUT", "path": f"/_ilm/policy/{ilm_name}", "asset": "ilm_policy"},
            {"action": "PUT", "path": f"/_ingest/pipeline/{pipeline_name}", "asset": "ingest_pipeline"},
        ]
        if assets.get("component_template_ecs_base"):
            plan.append({"action": "PUT", "path": f"/_component_template/{build_component_template_name(validated_prefix, 'ecs-base')}", "asset": "component_template_ecs_base"})
        if assets.get("component_template_settings"):
            plan.append({"action": "PUT", "path": f"/_component_template/{build_component_template_name(validated_prefix, 'settings')}", "asset": "component_template_settings"})
        plan.append({"action": "PUT", "path": f"/_index_template/{template_name}", "asset": "index_template"})
        if bootstrap_index:
            plan.append({"action": "PUT", "path": f"/_data_stream/{build_data_stream_name(validated_prefix)}", "asset": "data_stream"})
        if apply_kibana and assets.get("kibana_saved_objects"):
            objects = assets["kibana_saved_objects"].get("objects", [])
            for obj in objects:
                plan.append({"action": "POST", "path": f"/api/saved_objects/{obj.get('type')}/{obj.get('id')}", "asset": f"kibana:{obj.get('type')}"})
        return {
            "dry_run": True,
            "plan": plan,
            "plan_count": len(plan),
            "index_prefix": validated_prefix,
        }

    responses: dict[str, Any] = {
        "ilm_policy": es_request(config, "PUT", f"/_ilm/policy/{ilm_name}", assets["ilm_policy"]),
        "ingest_pipeline": es_request(config, "PUT", f"/_ingest/pipeline/{pipeline_name}", assets["ingest_pipeline"]),
    }

    if assets.get("component_template_ecs_base"):
        ecs_base_name = build_component_template_name(validated_prefix, "ecs-base")
        responses["component_template_ecs_base"] = es_request(
            config, "PUT", f"/_component_template/{ecs_base_name}", assets["component_template_ecs_base"]
        )
    if assets.get("component_template_settings"):
        settings_name = build_component_template_name(validated_prefix, "settings")
        responses["component_template_settings"] = es_request(
            config, "PUT", f"/_component_template/{settings_name}", assets["component_template_settings"]
        )

    responses["index_template"] = es_request(config, "PUT", f"/_index_template/{template_name}", assets["index_template"])
    responses["report_config"] = assets["report_config"]

    bootstrap_summary = None
    if bootstrap_index:
        bootstrap_summary = ensure_write_index(config, validated_prefix)

    kibana_summary = None
    if apply_kibana and kibana_url and assets.get("kibana_saved_objects"):
        kibana_summary = apply_kibana_saved_objects(
            config,
            kibana_url=kibana_url,
            kibana_space=kibana_space,
            bundle=assets["kibana_saved_objects"],
        )

    return {
        "assets_dir": str(assets_dir),
        "index_prefix": validated_prefix,
        "template_name": template_name,
        "pipeline_name": pipeline_name,
        "ilm_policy_name": ilm_name,
        "events_alias": build_events_alias(validated_prefix),
        "data_stream": build_data_stream_name(validated_prefix),
        "bootstrap_index": bootstrap_summary,
        "kibana": kibana_summary,
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
            kibana_url=args.kibana_url or None,
            kibana_space=args.kibana_space,
            apply_kibana=not args.skip_kibana_assets,
            dry_run=args.dry_run,
        )
        if summary.get("dry_run"):
            print(f"🔍 Dry-run: {summary['plan_count']} operation(s) would be applied")
            for step in summary["plan"]:
                print(f"   {step['action']} {step['path']}  ({step['asset']})")
            return 0
        print("✅ Elasticsearch assets applied")
        print(f"   data stream: {summary['data_stream']}")
        if summary["bootstrap_index"]:
            bs = summary["bootstrap_index"]
            print(f"   bootstrap: {bs.get('data_stream') or bs.get('write_index')} ({bs['status']})")
        if summary.get("kibana"):
            print(f"   kibana objects: {summary['kibana']['count']}")
        return 0
    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to apply Elasticsearch assets: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
