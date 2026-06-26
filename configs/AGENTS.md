# AGENTS.md

## Configuration

This directory contains example and default configuration only. `configs/dev.yaml` is the current local development/default config.

## Rules

- Do not commit real credentials.
- Do not commit private bucket names, private SDA paths, Globus tokens, AWS profiles, user-specific IU allocation values, or personal email addresses unless they are clearly placeholders.
- Provide shareable templates as `*.example.yaml`.
- Keep user-specific overrides in ignored `*.local.yaml` files.
- Support environment-variable expansion, especially `$USER` and the canonical data-root override `EML_DATA_ROOT`.
- Define data-root precedence explicitly when adding it. An environment override should replace the effective local data root while preserving relative Bronze/Silver/Gold/metadata paths.
- Keep local development config working.
- Add separate example configs for Quartz processing and transfer orchestration when those workflows are implemented.
- Any S3 handoff config must document temporary retention, explicit cleanup behavior, and lifecycle backup behavior, with a target maximum of 24 hours for project handoff objects.
- Document every config option added.
- Preserve the current `storage.base_dir` plus `paths.root` behavior unless a migration is documented and tested.
- Prototype branch config directories such as `deployments`, `sources`, and generated config examples can guide future templates, but keep final shareable files as explicit `*.example.yaml` templates with placeholders.
