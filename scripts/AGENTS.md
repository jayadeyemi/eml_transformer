# AGENTS.md

## Operational Scripts

This directory contains operational scripts. Scripts should be safe examples, not user-specific production secrets.

## Rules

- Do not hard-code IU usernames, project directories, allocation accounts, bucket names, SDA paths, Globus endpoint IDs, or email addresses.
- Use placeholders and comments for user-editable SLURM values.
- Heavy processing and transfer work must run as SLURM jobs, not login-node scripts.
- Future production examples should be split by concern: setup, AWS sandbox retrieval, short-lived S3 handoff validation, S3-to-Slate transfer, Slate processing, manifest/package creation, SDA transfer, temporary S3/Slate cleanup, and full dependency-based chained submission.
- SLURM scripts should use `set -euo pipefail`.
- SLURM scripts should write logs under a configurable Slate logs directory.
- Dependency submission should use `afterok` so downstream jobs only run after successful upstream jobs.
- GPU processing should be a separate script or an explicit switch.
- Avoid self-resubmitting jobs from inside job scripts unless clearly documented and explicitly desired.
- Prototype branch `scripts/aws*` workflows can guide preflight, cleanup, dry-run, and phase naming, but production scripts should remain placeholder-based and should not assume AWS/CDK deployment is required.
