# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| `main` (development) | ✅ |
| 0.1.0 tags and later | ✅ |

Earlier development history is not supported; upgrade to a tagged release or `main`.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's **private vulnerability reporting** feature on this repository
(*Security → Report a vulnerability*). It keeps the report, the discussion, and the fix
coordinated privately until a patched release is available. This project deliberately
lists no security email address; GitHub's private channel is the supported route.

Include what you can of: the affected version or commit, a minimal reproduction, and
your assessment of impact. You will get an acknowledgement and, where the report is
accepted, a timeline and credit if you want it.

## Scope

In scope:

- The CLI and every packaged command (`adlife …`), including untrusted-input handling
  for campaign and population YAML (safe loading, schema validation, asset-path
  containment beneath the project root).
- Prompt construction and secret handling in provider modes (minimised prompts, no
  filesystem paths or secrets in prompt text, log redaction).
- The report generator (script-tag safety of chart payloads, escaping of
  user-provided strings, absence of remote resources).
- The SQLite store and run-artifact integrity checks.

Out of scope:

- The live dashboard rendering hostile content in a local terminal beyond what any
  terminal application exposes.
- Vulnerabilities requiring a local attacker who can already modify the study
  directory (they control the artifacts by definition).
- Social engineering of users into running untrusted studies with hybrid providers
  enabled.

## Hardening posture (for reviewers)

- Campaign and population files are untrusted input: YAML is parsed with a safe loader,
  schemas are strict and versioned, and referenced asset paths are resolved and
  confirmed to lie beneath the project root before being opened.
- API keys are read only from environment variables or hidden prompts — never from
  committed files, never echoed to output.
- Logs redact authorization headers and likely secret patterns.
- Generated reports escape user-provided text and embed charts without remote scripts;
  chart JSON is escaped so no data value can close its own script tag.
- Every report carries the synthetic-data disclosure; the generator itself is offline.
