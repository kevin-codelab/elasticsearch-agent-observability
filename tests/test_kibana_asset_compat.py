"""Static compatibility checks for generated Kibana assets and ES|QL packs."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import render_es_assets  # noqa: E402


class KibanaAssetCompatibilityTests(unittest.TestCase):
    def test_saved_object_references_resolve_inside_bundle_or_data_view(self) -> None:
        bundle = render_es_assets.build_kibana_saved_objects("agent-obsv")
        object_ids = {(obj["type"], obj["id"]) for obj in bundle["objects"]}
        for obj in bundle["objects"]:
            for ref in obj.get("references", []):
                target = (ref["type"], ref["id"])
                self.assertIn(target, object_ids, f"unresolved reference {ref} in {obj['id']}")

    def test_search_saved_objects_have_parseable_search_source_json(self) -> None:
        bundle = render_es_assets.build_kibana_saved_objects("agent-obsv")
        searches = [obj for obj in bundle["objects"] if obj["type"] == "search"]
        self.assertGreaterEqual(len(searches), 4)
        for obj in searches:
            meta = obj["attributes"].get("kibanaSavedObjectMeta", {})
            source = json.loads(meta.get("searchSourceJSON", "{}"))
            self.assertEqual(source.get("indexRefName"), "kibanaSavedObjectMeta.searchSourceJSON.index")
            self.assertIn("query", source)

    def test_lens_state_uses_8_14_and_9_x_datasource_contract(self) -> None:
        bundle = render_es_assets.build_kibana_saved_objects("agent-obsv")
        for obj in [item for item in bundle["objects"] if item["type"] == "lens"]:
            state = obj["attributes"]["state"]
            datasource_states = state.get("datasourceStates", {})
            self.assertIn("formBased", datasource_states)
            self.assertNotIn("indexpattern", datasource_states)
            references = {ref["name"] for ref in obj.get("references", [])}
            self.assertIn("indexpattern-datasource-current-indexpattern", references)
            self.assertIn("indexpattern-datasource-layer-layer1", references)
            self.assertIn(obj["attributes"].get("visualizationType"), {"lnsXY", "lnsMetric", "lnsPie", "lnsDatatable"})

    def test_rendered_ndjson_saved_objects_are_line_delimited_json(self) -> None:
        discovery = {"detected_modules": [{"module_kind": "tool_registry"}], "files_scanned": 1}
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = render_es_assets.render_assets(discovery, Path(tmp_dir), index_prefix="agent-obsv", retention_days=30)
            ndjson_path = Path(paths["kibana_saved_objects_ndjson"])
            lines = [line for line in ndjson_path.read_text(encoding="utf-8").splitlines() if line]
        self.assertGreater(len(lines), 1)
        for line in lines:
            obj = json.loads(line)
            self.assertIn("type", obj)
            self.assertIn("id", obj)
            self.assertIn("attributes", obj)

    def test_esql_pack_avoids_version_fragile_conditional_aggregation(self) -> None:
        pack = render_es_assets.build_investigation_queries("agent-obsv")
        for item in pack["queries"]:
            query = item["query"]
            self.assertNotIn("COUNT(*) WHERE", query)
            self.assertNotIn("GROUP BY", query)
            self.assertIn("FROM agent-obsv-events*", query)
            self.assertIn("|", query)

    def test_query_rule_specs_have_minimum_kibana_rule_contract(self) -> None:
        specs = render_es_assets.build_alert_rule_specs("agent-obsv")
        for rule in specs["rules"]:
            self.assertEqual(rule["query_language"], "kuery")
            self.assertEqual(rule["index"], "agent-obsv-events*")
            self.assertEqual(rule["time_field"], "@timestamp")
            self.assertIn("threshold", rule)
            self.assertIn("group_by", rule["threshold"])


if __name__ == "__main__":
    unittest.main()
