# Security Policy

## Supported version

The `main` branch is the supported public version.

## Reporting a vulnerability

Please do **not** open a public GitHub Issue for security-sensitive problems.

Use one of these paths instead:

- GitHub Security Advisories / private vulnerability reporting, if enabled for the repository
- Direct contact through the repository owner profile on GitHub

When reporting a vulnerability, include:

- the affected script or generated asset type
- the impact
- a minimal reproduction
- whether the issue requires explicit unsafe flags or happens with default settings

## What counts as security-sensitive here

Examples include:

- credentials written to disk unexpectedly
- generated files exposing secrets or sensitive prompts by default
- unsafe network behavior in Elasticsearch or Kibana apply paths
- command execution paths that can be abused with untrusted input

## Handling expectations

Please give reasonable time for triage and a fix before public disclosure.
