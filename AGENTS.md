# AGENTS.md

## Project Contract

This repository is primarily a local-first Python/Typer data-processing pipeline for energy-related text. Preserve the existing Bronze -> Silver -> Gold medallion flow and the current CLI shape unless a user explicitly asks for a behavior change.

## Roadmap Context

- `plan.md` is a phase roadmap, not an always-on Codex instruction file. Read it when the user asks to follow, review, or continue the roadmap, or when they name a specific phase.
- Do not configure `plan.md` as a project instruction fallback file. Keep durable rules in `AGENTS.md` and task-specific phase sequencing in prompts or `plan.md`.

## Existing Behavior To Preserve

- Keep the current `eml_transformer.cli:app` Typer entrypoint and commands such as `sources`, `ingest`, `standardize`, `scrape`, `embed`, `run-all`, and `backfill`.
- Do not rewrite ingestion, source-specific parsing, standardization, scraping, embedding, dedupe, checkpoint, or scientific/data-processing behavior unless the change is required for path configurability, orchestration, or a user-requested fix.
- The current `run-all` command does not run scraping. Preserve that behavior unless a user explicitly asks for a CLI behavior change; production orchestration should call `scrape` explicitly when article text is required before embedding.
- Preserve local development defaults. The current default config uses `storage.base_dir: data` and `paths.root: .`, producing paths under `data/`.
- Preserve the relative record layout:
  - `bronze/source=<source>/records.jsonl`
  - `silver/source=<source>/<artifact>.parquet`
  - `gold/model=<model>/source=<source>/embeddings.parquet`
  - `metadata/dedupe/source=<source>.json`
  - `metadata/checkpoint/source=<source>.json`

## Storage And Transfer Boundaries

- External dataset discovery and historical staging belong in an acquisition-focused module. This is especially important for GDELT history, timestamp manifests, raw-file caching, partition filtering, and bounded aggregation before ingestion.
- Keep transfer concerns separate from processing concerns. AWS S3, SDA, Globus, DataSync, rsync, and transfer logic belong outside core ingestion, standardization, scraping, embedding, and source modules.
- Processing code should read and write under a configured data root and should not need to know whether data originated from AWS, local upload, or SDA.
- For Quartz production workflows, treat Slate as temporary active working storage for fast reads, writes, caches, logs, run state, intermediate outputs, and AI model/data-processing access.
- Treat AWS S3 as a short-lived retrieval handoff location, not durable staging, archive storage, or the active processing filesystem. S3 objects for this project should be temporary and cleaned up within the configured retention window, with a target maximum of 24 hours.
- Use explicit cleanup after successful transfer as the primary S3 cleanup step. S3 lifecycle expiration and incomplete-multipart cleanup are defense-in-depth, not a substitute for workflow cleanup.
- Treat the AWS sandbox as an execution environment for retrieval experiments/jobs that can place data into short-lived S3 handoff prefixes for Quartz/Slate to consume.
- Treat IU SDA as the long-term system of record for archived records and final verified outputs.
- Treat DataSync as optional and disabled unless an IU-supported route or task is explicitly confirmed and configured.
- Any future `EML_DATA_ROOT` or config-based root override must preserve the same relative layout and avoid accidental `data/data/...` nesting.

## Configuration And Secrets

- Do not hard-code or commit IU usernames, account IDs, allocation names, private project paths, email addresses, bucket names, S3 prefixes, SDA paths, Globus collection IDs, AWS profiles, credentials, or tokens.
- Use placeholders in examples and support environment-variable expansion, especially for user- and host-specific paths.
- Keep shareable configs as `*.example.yaml`; keep user-specific configs as ignored `*.local.yaml`.
- Never commit `.env`, `.aws/credentials`, AWS access keys, session tokens, Globus tokens, private SDA details, large data, logs, run metadata, archives, transfer state, or cache directories.

## HPC And Production Runs

- Heavy transfer, processing, packaging, checksumming, archive upload, and embedding work must run under SLURM, not on login nodes.
- SLURM examples should be editable templates with placeholders for account, partition, time, memory, CPUs, GPUs, email, project paths, and logs.
- Prefer dependency-based chained submission with `afterok` for production workflows.
- Production runs should record run ID, timestamp, git commit when available, resolved config, commands, logs, temporary S3 handoff prefixes, Slate working paths, SDA archive paths, manifest paths, checksums, transfer logs, Globus task ID when applicable, cleanup status, and final status.
- Workflows should be safe to rerun.

## Tests

- Normal automated tests must not require live AWS, Globus, SDA, IU accounts, or network access.
- Mock AWS CLI, Globus CLI, SDA, filesystem transfer backends, and SLURM command construction.
- When changing paths or storage behavior, test that local defaults still work and that root overrides preserve the relative record layout.

## Branch Prototype Guidance

- Branches such as `implementation-1` and `implementation-2` contain useful prototype ideas, but they are incomplete and should not be copied wholesale.
- Useful branch ideas include GDELT discovery manifests, partition-aware staging, transfer aggregation, config templates, offline tests, and dry-run operational scripts.
- Do not adopt branch patterns that make AWS/CDK/deployment the default workflow. Cloud infrastructure must remain optional, and the main production design remains AWS sandbox retrieval -> short-lived S3 handoff -> Quartz/Slate processing -> SDA archive.
