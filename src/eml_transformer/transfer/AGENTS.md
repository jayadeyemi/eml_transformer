# AGENTS.md

## Transfer Boundary

This directory owns data movement between AWS sandbox/S3 handoff prefixes, Slate, and SDA. It must not contain scientific processing logic.

## Expected Backends

- AWS S3 backend using AWS CLI `s3 sync` as the default mechanism for short-lived S3 handoff to Slate transfer.
- SDA backend abstraction.
- Globus SDA backend when endpoint IDs and authentication are explicitly configured.
- Filesystem or mounted-path backend if IU exposes a mounted SDA path.
- Manual handoff backend for safe documented fallback workflows.
- Optional DataSync backend only as a disabled advanced backend after an IU-supported route is confirmed.

## Rules

- Do not store credentials, tokens, profiles, private bucket names, or private endpoint IDs.
- Do not assume AWS CLI exists; provide readiness checks.
- Do not assume AWS credentials exist; readiness checks should validate identity and access when selected.
- Do not treat S3 as durable project storage. S3 transfer plans should include configured retention and explicit cleanup steps, with a target maximum of 24 hours for project handoff objects.
- Use S3 lifecycle expiration and incomplete-multipart cleanup as safety nets. Do not rely on lifecycle rules as the only cleanup mechanism for temporary handoff prefixes.
- S3-to-Slate sync plans should not include deletion flags such as `--delete` by default. If mirroring deletion is ever needed, require an explicit destructive option and a tightly scoped destination.
- Prefer conservative S3-to-local sync options such as `--dryrun` for planning and `--exact-timestamps` when timestamp fidelity matters.
- Do not assume Globus CLI exists or is authenticated.
- Do not invent IU collection IDs, SDA paths, DataSync routes, transfer nodes, quotas, or allocation names.
- Prefer Globus checksum-oriented transfer options for SDA handoff when supported by the configured collections.
- All commands must support dry-run behavior when meaningful.
- Commands should be logged in run metadata.
- Errors should explain what tool, credential, endpoint, path, permission, or config value is missing.
- Command construction must be separately testable from command execution.
- Normal tests must mock subprocess calls and external services.
- Prototype branch scripts and storage-transfer helpers may guide command construction, dry-run behavior, and aggregate manifests, but final transfer code should stay here rather than in `storage`, `cloud`, or `deployment`.
