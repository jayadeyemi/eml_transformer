# AGENTS.md

## Orchestration Boundary

This directory owns high-level workflow coordination, readiness checks, resolved configuration, run metadata, and environment validation.

## Rules

- Do not place scientific transformation logic here.
- Call existing CLI commands or internal pipeline functions instead of duplicating ingestion, standardization, scraping, embedding, or backfill behavior.
- A full workflow should be decomposable into independent steps: AWS sandbox retrieval, short-lived S3 handoff validation, S3-to-Slate transfer, Slate processing/model access, manifest generation, archive packaging, SDA transfer, and cleanup of temporary S3/Slate working data.
- Cleanup of temporary S3 handoff prefixes must be an explicit workflow step after successful transfer or after a safe failed-run decision. Lifecycle expiration is only a backup.
- Readiness checks must be non-mutating.
- Readiness checks should validate host context, compute versus login node when detectable, writable Slate paths, AWS CLI, AWS identity, S3 access, Globus CLI if selected, Globus authentication if selected, SDA destination configuration, Python dependencies, and available local space or quota when detectable.
- Each production run should create a run directory or metadata record.
- Run state should include timestamp, run ID, git commit hash when available, resolved config, commands, logs, transfer summaries, temporary S3 handoff prefixes and cleanup status, Slate working paths, manifest path, archive checksum, SDA destination, Globus task ID when applicable, and success/failure status.
- Failed runs should leave enough information for diagnosis and safe retry.
- `BackfillPipeline.run_all()` currently has a list/dict results bug; add tests and fix that before relying on `--source all` backfills in production orchestration.
- The current CLI `run-all` command omits scraping. Production workflows that need scraped article text before embedding should call ingestion, standardization, scraping, and embedding as separate explicit steps.
- Historical acquisition, including GDELT partition planning and filtering, should be orchestrated as separate resumable steps before normal ingestion.
- Prototype branch deployment/service modules should be treated as design references only. Do not create a long-lived service layer unless it removes real orchestration complexity and has tests.
