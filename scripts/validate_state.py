#!/usr/bin/env python3
"""Validate that live Elasticsearch assets match the local generated definitions.

Compares ILM policy, ingest pipeline, component templates, and index template
between a running ES cluster and the generated JSON files on disk.
Reports drift as structured JSON or human-readable text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import (
    ESConfig,
    SkillError,
    build_component_template_name,
    build_data_stream_name,
    es_request,
    print_error,
    read_json,
    validate_credential_pair,
    validate_index_prefix,
    validate_workspace_dir,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect configuration drift between local assets and live ES cluster")
    parser.add_argument("--assets-dir", required=True, help="Path to generated elasticsearch/ directory")
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--es-user", default="")
    parser.add_argument("--es-password", default="")
    parser.add_argument("--index-prefix", default="agent-obsv")
    parser.add_argument("--output", help="Optional output file (JSON)")
    parser.add_argument("--output-format", choices=["json", "text"], default="text")
    parser.add_argument("--no-verify-tls", action="store_true")
    return parser.parse_args()


def _deep_compare(local: Any, remote: Any, path: str = "") -> list[dict[str, Any]]:
    """Recursively compare two nested structures, returning a list of diffs.

    Only keys present in *local* are checked. Extra keys in remote (ES metadata
    like ``version``, ``in_use_by``, etc.) are silently ignored to avoid false
    drift reports.
    """
    diffs: list[dict[str, Any]] = []
    if isinstance(local, dict) and isinstance(remote, dict):
        for key in sorted(local.keys()):
            child_path = f"{path}.{key}" if path else key
            if key not in remote:
                diffs.append({"path": child_path, "type": "missing_in_remote", "local": local[key]})
            else:
                diffs.extend(_deep_compare(local[key], remote[key], child_path))
    elif isinstance(local, list) and isinstance(remote, list):
        if len(local) != len(remote):
            diffs.append({"path": path, "type": "list_length_mismatch", "local_len": len(local), "remote_len": len(remote)})
        for i in range(min(len(local), len(remote))):
            diffs.extend(_deep_compare(local[i], remote[i], f"{path}[{i}]"))
    elif local != remote:
        diffs.append({"path": path, "type": "value_mismatch", "local": local, "remote": remote})
    return diffs


def _fetch_ilm(config: ESConfig, ilm_name: str) -> dict[str, Any] | None:
    try:
        result = es_request(config, "GET", f"/_ilm/policy/{ilm_name}")
        return result.get(ilm_name, {})
    except SkillError:
        return None


def _fetch_pipeline(config: ESConfig, pipeline_name: str) -> dict[str, Any] | None:
    try:
        result = es_request(config, "GET", f"/_ingest/pipeline/{pipeline_name}")
        return result.get(pipeline_name, {})
    except SkillError:
        return None


def _fetch_component_template(config: ESConfig, template_name: str) -> dict[str, Any] | None:
    try:
        result = es_request(config, "GET", f"/_component_template/{template_name}")
        templates = result.get("component_templates", [])
        for tpl in templates:
            if tpl.get("name") == template_name:
                return tpl.get("component_template", {})
        return None
    except SkillError:
        return None


def _fetch_index_template(config: ESConfig, template_name: str) -> dict[str, Any] | None:
    try:
        result = es_request(config, "GET", f"/_index_template/{template_name}")
        templates = result.get("index_templates", [])
        for tpl in templates:
            if tpl.get("name") == template_name:
                return tpl.get("index_template", {})
        return None
    except SkillError:
        return None


def validate_state(config: ESConfig, *, assets_dir: Path, index_prefix: str) -> dict[str, Any]:
    """Compare local assets against live ES cluster, returning a structured drift report."""
    validated_prefix = validate_index_prefix(index_prefix)
    resolved_dir = validate_workspace_dir(assets_dir, "Assets directory")

    checks: list[dict[str, Any]] = []

    # ILM policy
    ilm_name = f"{validated_prefix}-lifecycle"
    local_ilm_path = resolved_dir / "ilm-policy.json"
    if local_ilm_path.exists():
        local_ilm = read_json(local_ilm_path)
        remote_ilm = _fetch_ilm(config, ilm_name)
        if remote_ilm is None:
            checks.append({"asset": "ilm_policy", "name": ilm_name, "status": "not_found_in_cluster"})
        else:
            diffs = _deep_compare(local_ilm, remote_ilm)
            checks.append({
                "asset": "ilm_policy",
                "name": ilm_name,
                "status": "drifted" if diffs else "in_sync",
                "diff_count": len(diffs),
                "diffs": diffs[:20],
            })

    # Ingest pipeline
    pipeline_name = f"{validated_prefix}-normalize"
    local_pipeline_path = resolved_dir / "ingest-pipeline.json"
    if local_pipeline_path.exists():
        local_pipeline = read_json(local_pipeline_path)
        remote_pipeline = _fetch_pipeline(config, pipeline_name)
        if remote_pipeline is None:
            checks.append({"asset": "ingest_pipeline", "name": pipeline_name, "status": "not_found_in_cluster"})
        else:
            diffs = _deep_compare(local_pipeline, remote_pipeline)
            checks.append({
                "asset": "ingest_pipeline",
                "name": pipeline_name,
                "status": "drifted" if diffs else "in_sync",
                "diff_count": len(diffs),
                "diffs": diffs[:20],
            })

    # Component templates
    for component in ("ecs-base", "settings"):
        ct_name = build_component_template_name(validated_prefix, component)
        local_path = resolved_dir / f"component-template-{component}.json"
        if local_path.exists():
            local_ct = read_json(local_path)
            remote_ct = _fetch_component_template(config, ct_name)
            if remote_ct is None:
                checks.append({"asset": "component_template", "name": ct_name, "status": "not_found_in_cluster"})
            else:
                diffs = _deep_compare(local_ct, remote_ct)
                checks.append({
                    "asset": "component_template",
                    "name": ct_name,
                    "status": "drifted" if diffs else "in_sync",
                    "diff_count": len(diffs),
                    "diffs": diffs[:20],
                })

    # Index template
    template_name = f"{validated_prefix}-events-template"
    local_it_path = resolved_dir / "index-template.json"
    if local_it_path.exists():
        local_it = read_json(local_it_path)
        remote_it = _fetch_index_template(config, template_name)
        if remote_it is None:
            checks.append({"asset": "index_template", "name": template_name, "status": "not_found_in_cluster"})
        else:
            diffs = _deep_compare(local_it, remote_it)
            checks.append({
                "asset": "index_template",
                "name": template_name,
                "status": "drifted" if diffs else "in_sync",
                "diff_count": len(diffs),
                "diffs": diffs[:20],
            })

    drifted_count = sum(1 for c in checks if c.get("status") == "drifted")
    missing_count = sum(1 for c in checks if c.get("status") == "not_found_in_cluster")
    return {
        "index_prefix": validated_prefix,
        "total_checks": len(checks),
        "in_sync": sum(1 for c in checks if c.get("status") == "in_sync"),
        "drifted": drifted_count,
        "not_found": missing_count,
        "overall_status": "drifted" if drifted_count > 0 else ("incomplete" if missing_count > 0 else "in_sync"),
        "checks": checks,
    }


def render_text(report: dict[str, Any]) -> str:
    status_icon = {"in_sync": "✅", "drifted": "⚠️", "incomplete": "❓", "not_found_in_cluster": "❌"}
    lines = [
        f"{status_icon.get(report['overall_status'], '?')} Overall: {report['overall_status']} "
        f"({report['in_sync']} in sync, {report['drifted']} drifted, {report['not_found']} missing)",
        "",
    ]
    for check in report["checks"]:
        icon = status_icon.get(check["status"], "?")
        line = f"  {icon} {check['asset']}: {check['name']} — {check['status']}"
        if check.get("diff_count"):
            line += f" ({check['diff_count']} diff(s))"
        lines.append(line)
        for diff in check.get("diffs", [])[:5]:
            lines.append(f"      {diff['type']} at {diff['path']}")
    return "\n".join(lines)


def main() -> int:
    try:
        args = parse_args()
        credentials = validate_credential_pair(args.es_user, args.es_password)
        config = ESConfig(
            es_url=args.es_url,
            es_user=credentials[0] if credentials else None,
            es_password=credentials[1] if credentials else None,
            verify_tls=not args.no_verify_tls,
        )
        report = validate_state(
            config,
            assets_dir=Path(args.assets_dir).expanduser().resolve(),
            index_prefix=args.index_prefix,
        )
        if args.output:
            output_path = Path(args.output).expanduser().resolve()
            if args.output_format == "json":
                write_json(output_path, report)
            else:
                output_path.write_text(render_text(report) + "\n", encoding="utf-8")
            print(f"✅ drift report written: {output_path}")
        else:
            print(render_text(report))
        return 0 if report["overall_status"] == "in_sync" else 2
    except SkillError as exc:
        print_error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        print_error(f"Drift detection failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
