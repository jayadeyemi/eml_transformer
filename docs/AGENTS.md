# AGENTS.md

## Documentation

This directory contains user-facing workflow documentation.

## Rules

- Existing docs describe the local Bronze -> Silver -> Gold pipeline; preserve that explanation when adding production workflow docs.
- Explain the AWS sandbox retrieval -> short-lived S3 handoff -> Quartz/Slate processing -> SDA archive architecture.
- Explain why Slate is temporary active working storage for fast retrieval by AI models and data-processing jobs.
- Explain why S3 is only a temporary handoff layer for retrieved data, with explicit cleanup as the primary cleanup control and lifecycle expiration as a backup.
- Explain why SDA is the long-term archive and system of record.
- Explain why AWS CLI `s3 sync` is the default S3 handoff transfer method.
- Explain why Globus is preferred for SDA when available.
- Explain why DataSync is optional and institution-dependent.
- Include setup, config, dry-run, SLURM submission, dependency chaining, resume/retry, manifest verification, archive creation, and troubleshooting instructions.
- Include a clear "Ask IU RT for this information" section.
- Do not claim IU supports a specific Globus collection, SDA mount, DataSync route, transfer node, quota, or limit unless repo context explicitly confirms it.
- Keep commands generic and placeholder-based.
- Prototype branch docs for AWS S3 layout, storage transfer, and local setup may guide future documentation, but docs must be revised for the Slate-first design before being treated as authoritative.
