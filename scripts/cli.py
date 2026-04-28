#!/usr/bin/env python3
"""Unified CLI for elasticsearch-agent-observability.

Usage:
    agent-obsv init       — bootstrap the full stack (index template + ILM + pipeline + dashboard + Collector + bridge)
    agent-obsv status     — what's deployed on the cluster
    agent-obsv doctor     — honest end-to-end pipeline diagnostic
    agent-obsv alert      — alert check with root-cause analysis
    agent-obsv query      — pre-built ES query templates
    agent-obsv report     — generate a smoke/metrics report
    agent-obsv validate   — configuration drift detection
    agent-obsv uninstall  — remove all managed assets
    agent-obsv quickstart — guided one-command setup for common agent frameworks
    agent-obsv scenarios  — show "I want to do X → run Y" cheat sheet
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts/ is on sys.path so module imports resolve.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


COMMANDS: dict[str, tuple[str, str]] = {
    "init":       ("bootstrap_observability", "Bootstrap the full observability stack"),
    "quickstart": ("quickstart",              "Guided one-command setup for common agent frameworks"),
    "status":     ("status",                  "Report what assets are deployed on the cluster"),
    "doctor":     ("doctor",                  "Honest end-to-end pipeline diagnostic"),
    "alert":      ("alert_and_diagnose",      "Alert check with intelligent root-cause analysis"),
    "eval":       ("evaluate",                "Run regression evaluators against recent traces"),
    "replay":     ("replay",                  "Session replay — nested span tree from ES traces"),
    "query":      ("query",                   "Pre-built ES query templates"),
    "report":     ("generate_report",         "Generate a smoke/metrics report"),
    "validate":   ("validate_state",          "Configuration drift detection"),
    "uninstall":  ("uninstall",               "Remove all managed assets from the cluster"),
}


SCENARIOS = """\
┌─────────────────────────────────────────────────────────────┐
│ I want to …                         │ Run                   │
├─────────────────────────────────────────────────────────────┤
│ Set up observability from scratch    │ agent-obsv init       │
│ Quick setup for a known framework    │ agent-obsv quickstart │
│ Check if the pipeline is healthy     │ agent-obsv doctor     │
│ See what's deployed on the cluster   │ agent-obsv status     │
│ Find out why my agent is slow        │ agent-obsv alert      │
│ See token spend breakdown            │ Open Kibana dashboard │
│ Query traces for a specific session  │ agent-obsv query      │
│ Check for config drift               │ agent-obsv validate   │
│ Generate a metrics report            │ agent-obsv report     │
│ Remove everything cleanly            │ agent-obsv uninstall  │
└─────────────────────────────────────────────────────────────┘

Common workflows:

  # First-time setup for a Python agent project
  agent-obsv quickstart --agent-dir /path/to/agent --framework auto

  # Bootstrap + apply to a real cluster
  agent-obsv init --workspace /path/to/agent --output-dir ./generated/bootstrap \\
    --es-url http://localhost:9200 --es-user elastic --es-password <pwd> \\
    --apply-es-assets --kibana-url http://localhost:5601 --apply-kibana-assets

  # Check pipeline health (don't trust /healthz)
  agent-obsv doctor --es-url http://localhost:9200

  # Run alert check every 15 min
  agent-obsv alert --es-url http://localhost:9200 --time-range now-15m --write-to-es

  # Load custom alert rules from a config file
  agent-obsv alert --es-url http://localhost:9200 --alert-rules rules.json
"""


def _print_help() -> None:
    print("agent-obsv — unified CLI for elasticsearch-agent-observability")
    print()
    print("Usage: agent-obsv <command> [options]")
    print()
    print("Commands:")
    max_name_len = max(len(name) for name in COMMANDS)
    for name, (_, description) in COMMANDS.items():
        print(f"  {name:<{max_name_len + 2}} {description}")
    print()
    print(f"  {'scenarios':<{max_name_len + 2}} Show scenario → command cheat sheet")
    print()
    print("Run `agent-obsv <command> --help` for command-specific options.")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        return 0

    command = sys.argv[1].lower().strip()

    if command == "scenarios":
        print(SCENARIOS)
        return 0

    if command not in COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Run `agent-obsv --help` for available commands.", file=sys.stderr)
        return 1

    module_name, _ = COMMANDS[command]

    # Rewrite sys.argv so the target script sees itself as the entrypoint.
    sys.argv = [f"agent-obsv {command}"] + sys.argv[2:]

    try:
        module = __import__(module_name)
        return module.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        print(f"❌ {command} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
