# AGENTS.md

## Archive Boundary

This directory owns manifest generation, checksum verification, archive naming, and package creation for SDA as the long-term archive. It must not contain ingestion, standardization, scraping, or embedding logic.

## Rules

- Generate manifests before archival.
- Manifests should include relative path, size, modification time, SHA-256 checksum, logical stage, run ID, timestamp, temporary source S3 handoff URI when applicable, Slate working path when applicable, SDA archive destination when applicable, cleanup status when applicable, and git commit hash when available.
- Preserve and include compatibility-critical metadata such as dedupe state and checkpoints.
- Support at least tar and tar.gz packaging.
- Prefer uncompressed tar as a safe default for large or already-compressed research datasets.
- Archive names should include project name, run ID, timestamp, and optionally git commit hash or dataset label.
- Do not overwrite prior archives unless explicitly configured.
- Archive generation must be rerunnable and auditable.
- Normal tests must not require SDA access.
