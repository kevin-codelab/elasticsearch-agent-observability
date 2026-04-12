# Contributing

Thanks for improving `elasticsearch-agent-observability`.
Keep changes small, testable, and honest about current behavior.

## Ground rules

- Keep public claims aligned with what the repository actually generates today.
- Do not expand promises in `README.md` or `SKILL.md` without matching implementation and tests.
- Do not commit secrets, real credentials, or generated files that contain environment-specific data.
- Prefer environment-variable placeholders over inline credentials.
- Keep generated asset shape stable unless there is a clear migration reason.

## Local workflow

```bash
python3 -m unittest discover -s tests
```

The repository scripts use the Python standard library only.
If you change the generated instrumentation snippet, make sure the dependency note in `README.md` still matches reality.

## Pull request checklist

- Add or update tests for behavior changes
- Update `README.md`, `SKILL.md`, or `references/` when public behavior changes
- Keep new command flags documented
- Keep security-sensitive defaults conservative

## Scope discipline

This repository bootstraps the Elasticsearch and Kibana side of agent observability.
Do not describe it as a full observability platform unless the implementation actually reaches that bar.

## Reporting problems

- Use GitHub Issues for bugs, regressions, and documentation problems
- Use `SECURITY.md` for sensitive security reports
