# EML Transformer AWS/Quartz/SDA Implementation Plan

This plan is the roadmap after the AGENTS-only guidance step. Implement phases incrementally. Do not skip validation, and do not mix unrelated phases unless a later user request explicitly asks for a larger combined change.

Use this file as task context when a prompt asks to follow, review, or continue the roadmap, or when a prompt names a specific phase. Do not configure `plan.md` as a Codex project-instruction fallback file; durable rules belong in `AGENTS.md`, while this file carries sequencing and implementation context.

## Target Workflow

The desired production workflow is:

```text
AWS sandbox retrieval job
-> short-lived AWS S3 handoff bucket or prefix, target retention <= 24 hours
-> transfer retrieved data to Quartz working storage on Slate
-> run the existing eml_transformer processing steps on Quartz
-> use Slate as temporary fast working storage for model/data-processing access
-> package, manifest, verify, and archive final records/outputs to IU SDA
-> explicitly clean up temporary S3 handoff and Slate working data according to retention policy
```

The project must continue to support local development, Quartz-only processing, AWS sandbox retrieval, short-lived S3 handoff, SDA archival, full production runs, dry runs, cleanup, and repeatable SLURM-based batch operation.

## Global Rules For Every Phase

- Preserve the current Bronze/Silver/Gold processing behavior unless a change is explicitly required for configurability, orchestration, or a user-requested bug fix.
- Keep transfer, archive, orchestration, storage, and processing responsibilities separate.
- Do not hard-code IU usernames, account IDs, allocation names, emails, Slate paths, SDA paths, bucket names, AWS profiles, credentials, Globus endpoint IDs, DataSync routes, or private prefixes.
- Do not commit secrets or user-specific local configs.
- Keep local development defaults working.
- Normal automated tests must not require AWS, Globus, SDA, IU accounts, live credentials, or network access.
- Heavy transfer, processing, packaging, checksumming, archive upload, and embedding work must run under SLURM in production.
- Treat AWS S3 as a temporary handoff location for retrieved data, with target retention of no more than 24 hours.
- Use explicit cleanup after successful handoff as the primary S3 cleanup step. S3 lifecycle expiration and incomplete-multipart cleanup are safety nets, not substitutes for workflow cleanup.
- Treat Slate as temporary active working storage for fast retrieval by AI models and data-processing jobs on Quartz.
- Treat SDA as the long-term archive and system of record.
- Treat DataSync as optional and disabled unless an IU-supported route is explicitly confirmed and configured.

## Current Repo Facts To Preserve

- CLI entrypoint: `eml_transformer.cli:app`.
- Current CLI commands: `sources`, `ingest`, `standardize`, `scrape`, `embed`, `run-all`, and `backfill`.
- Current `run-all` behavior runs ingestion, standardization, and embedding, but not scraping. Preserve that behavior unless a user explicitly asks to change it; production orchestration should call `scrape` explicitly when `embedding_input: articles` requires article text.
- Current default local storage behavior:
  - `configs/dev.yaml` has `storage.base_dir: data`.
  - `StoragePaths.root` is built from `paths.root`, currently `"."`.
  - Together, local outputs are written under `data/`.
- Current relative data layout:
  - `bronze/source=<source>/records.jsonl`
  - `silver/source=<source>/<artifact>.parquet`
  - `gold/model=<model>/source=<source>/embeddings.parquet`
  - `metadata/dedupe/source=<source>.json`
  - `metadata/checkpoint/source=<source>.json`
- Existing S3 storage code is not enough for production processing because JSONL append/read support, imports, and dependency declarations are incomplete. Prefer S3 transfer modules for temporary handoff transfer, not active S3 processing storage.

## Data Retrieval, Indexing, GDELT History, And Staging Notes

Data currently retrieved:

- `iem_afos`: archived NWS AFOS text products from Iowa Environmental Mesonet. It fetches product identifiers formed from product types such as `AFD`, `HWO`, `NPW`, `WSW`, `LSR`, `SPS` and configured WFOs, then parses weather text products into standardized text records.
- `newsapi`: NewsAPI `/v2/everything` article records using the configured energy/grid query, language, sort, page size, and date window.
- `miso_notifications`: grouped MISO market/operations notifications from the MISO notifications API.
- `weather_alerts`: active weather.gov alert features by configured state/area.
- `gdelt`: GDELT 2.x GKG files for energy, grid, severe weather, outage, organization, and MISO-footprint location filtering. GDELT standardized records initially contain article metadata and URLs; article text is expected to be added by the scraper into the `articles` artifact before embedding.

Current indexing and state:

- Bronze indexing is source-level only: one append-only `records.jsonl` per source.
- Bronze rows contain `source`, `run_id`, `retrieved_at`, `raw_record_hash`, and `raw`.
- Dedupe indexing is by stable raw-record hash in `metadata/dedupe/source=<source>.json`.
- Checkpoint indexing is per source in `metadata/checkpoint/source=<source>.json`.
- Silver indexing is one Parquet artifact per source and artifact name, deduped by `record_id`.
- Gold indexing is by model and source, with embeddings deduped by `record_id`.
- GDELT `record_id` is currently `GKGRECORDID`.
- Current storage is not date-partitioned, so large GDELT history should not be aggregated through one in-memory or one-file path for production.

Current GDELT history method:

- `GDELTSource.fetch_records(from_date, to_date)` builds 15-minute timestamps across the date window.
- Each day maps to 96 GKG files.
- Each timestamp is currently downloaded from `http://data.gdeltproject.org/gdeltv2/{timestamp}.gkg.csv.zip`; future acquisition code should prefer HTTPS when available and keep the scheme configurable only if needed.
- Files are downloaded in parallel with up to 8 workers, unzipped in memory, read as tab-separated GKG rows, annotated with `GDELT_TIMESTAMP` and `GDELT_URL`, filtered in memory, then returned to the existing ingestion pipeline.
- `scripts/download_gdelt.py` is only a stratified sample downloader. It samples a few days per month and a few 15-minute files per day, then writes a sample Parquet file under `data/samples/`.
- Backfill uses date windows and calls ingestion with checkpoints disabled, but current GDELT normal incremental checkpointing is incomplete because GDELT does not expose a reliable `get_checkpoint_value`.
- `configs/dev.yaml` currently parses `sources.gdelt.ingestion.target_themes` as one string, not a list, because the YAML entries are missing a space after `-`. Fix this before relying on GDELT filters for production staging.

Desired AWS/GDELT retrieval and handoff behavior:

- The AWS sandbox can run retrieval jobs and place retrieved files into short-lived S3 handoff prefixes.
- AWS S3 should not be durable project storage. Project objects in S3 should be temporary and explicitly cleaned up within the configured retention window, with a target maximum of 24 hours. S3 lifecycle expiration should be configured as a backup safety net.
- Slate should hold temporary working copies for fast processing/model access on Quartz.
- SDA should hold long-term archived records and final verified outputs.
- For GDELT history, the ideal flow is:
  1. Build a date/timestamp manifest for the requested GDELT dataset and date range.
  2. Run retrieval either directly on Quartz/Slate or in the AWS sandbox.
  3. If the AWS sandbox performs retrieval, write raw files to a short-lived S3 handoff prefix.
  4. Transfer retrieved files from S3 handoff into partitioned Slate working storage.
  5. Filter each Slate partition independently.
  6. Aggregate filtered partitions into daily or monthly standardized input batches.
  7. Feed those batches through the existing ingestion, standardization, scraping when needed, and embedding steps without downstream code needing to know whether records came from live HTTP or Slate partitions.
  8. Archive final verified records and manifests to SDA.
  9. Explicitly clean up temporary S3 handoff objects and Slate working data according to retention policy.
- Retain raw downloaded GDELT ZIPs at least until filtered outputs and manifests are verified. If future filters must be rerunnable without re-querying GDELT, archive the raw ZIPs or a raw-file package to SDA; if only manifests are archived, re-filtering requires re-download.
- Partition GDELT raw files by dataset and timestamp, for example:
  - `external/source=gdelt/dataset=gkg/year=YYYY/month=MM/day=DD/hour=HH/minute=MM/<timestamp>.gkg.csv.zip`
- Partition filtered GDELT outputs by date before aggregation, for example:
  - `staged/source=gdelt/dataset=gkg_filtered/year=YYYY/month=MM/day=DD/part-*.parquet`
- Aggregate filtered partitions into bounded artifacts, not all history at once:
  - daily for ingestion/backfill handoff
  - monthly only after daily validation succeeds
  - never full multi-year in memory
- Preserve aggregation manifests with source file URI, local Slate path, optional temporary S3 handoff URI, row counts, filtered row counts, failures, checksum, run ID, archive destination, and cleanup status.
- Download partitioning should support SLURM arrays by day, month, source, or timestamp shard.
- Re-runs should skip already verified partitions unless `--force` is explicitly configured.
- The seamless integration point should be a source/input adapter, not a rewrite of downstream processing. GDELT ingestion should support live HTTP and local/staged partition origins while still returning source-native records into the existing ingestion pipeline. Short-lived S3 handoff is a transfer/acquisition origin and should be materialized to Slate/local partitions before ingestion consumes it.
- The code path should allow:
  - local development with small sampled partitions
  - Quartz backfill from Slate partitions
  - AWS sandbox retrieval into short-lived S3 handoff prefixes
  - S3-to-Slate sync of temporarily retrieved data
  - SDA archival of final verified records and manifests
  - identical standardization, scraping, and embedding behavior after ingestion
- For very broad multi-year GDELT exploration, GDELT datasets in Google BigQuery are a reasonable external discovery/filtering option if a future user explicitly accepts GCP credentials, costs, and governance. Do not make BigQuery or GCP a default dependency for this AWS/Quartz/SDA roadmap.

## Existing Branch Prototype Comparison

The local repository has prototype branches that are useful as references but should not be merged or copied wholesale:

- `implementation-1` adds early AWS/CDK deployment files, AWS test scripts, deployment configs, a GDELT acquisition prototype, and several pipeline fixes. It also deletes some current notebooks and scraping/GDELT source files, so it is not a safe implementation base.
- `implementation-2` is the most useful reference. It adds `acquisition/gdelt/discovery.py`, `storage/transfer.py`, optional CDK infrastructure, source/deployment config templates, AWS validation/cleanup helpers, and offline-oriented tests. It also leans toward AWS/deployment abstractions that should remain optional for the new Slate-first design.
- `cloud-engineer-training` mostly removes or changes exploratory assets and is not a useful design base for the desired workflow.
- `upstream/gdelt` changes only a small set of scraping/embedding-related files and does not address the AWS/Slate/SDA architecture.

Design signals to keep:

- GDELT history should have a dedicated acquisition/discovery layer for timestamp planning, raw-file caching, candidate URL extraction, parser/filter versioning, and per-file manifests.
- Transfer aggregation is useful for moving many small JSON/Parquet objects, but the final design should place transfer planning in `src/eml_transformer/transfer/` and historical partition aggregation in `src/eml_transformer/acquisition/` or `src/eml_transformer/archive/`, not in storage by default.
- Branch test categories such as unit, contract, integration, and AWS-contract tests are useful, but normal tests must remain offline-safe.
- Optional CDK and AWS deployment scripts may guide future examples, but cloud infrastructure must not become required for local or Quartz/Slate processing. S3 examples must model short-lived handoff, not durable storage.

Folder decisions from branch comparison:

- Add and keep `src/eml_transformer/acquisition/` as the future home for historical dataset discovery and staging.
- Keep `src/eml_transformer/transfer/`, `archive/`, and `orchestration/` as already planned.
- Do not create `src/eml_transformer/cloud/`, `deployment/`, `commands/`, or `services/` unless a later implementation need is proven. Their branch prototypes should be mined for ideas and then folded into the planned boundaries.
- Do not create `infra/cdk/` unless optional AWS infrastructure templates are explicitly requested.

## Phase 0: Agent Guidance Baseline

Status: completed in the current working tree.

Scope:

- Add root and nested `AGENTS.md` files.
- Create only missing guidance directories:
  - `src/eml_transformer/acquisition/`
  - `src/eml_transformer/transfer/`
  - `src/eml_transformer/archive/`
  - `src/eml_transformer/orchestration/`
- Do not implement pipeline changes in this phase.

Done criteria:

- AGENTS files exist at root, package, acquisition, storage, transfer, archive, orchestration, configs, scripts, infra, tests, and docs levels.
- `git diff --check` passes.

## Phase 1: Safety Baseline And Existing Pipeline Stabilization

Objective:

Make the current local pipeline safer to modify before adding transfer and orchestration layers.

Implementation instructions:

- Add focused tests around current path generation, local storage behavior, source config loading, checkpoint/dedupe state, and pipeline result summaries.
- Fix the known `BackfillPipeline.run_all()` bug where `results` is initialized as a list but assigned with string keys.
- Add or update tests for `backfill --source all` with mocked sources.
- Add tests proving disabled sources are filtered by runtime source config construction.
- Add tests proving local defaults still produce the current relative layout under `data/`.
- Fix and test the GDELT `target_themes` config shape so it is a list, not one comma-separated string.
- Add tests for GDELT timestamp generation, record filtering, and source-native record IDs without downloading live files.
- Do not introduce AWS, Globus, SDA, or SLURM behavior yet.

Likely files:

- `src/eml_transformer/pipelines/backfill_pipeline.py`
- `src/eml_transformer/runtime.py`
- `src/eml_transformer/utils/config.py`
- `src/eml_transformer/storage/paths.py`
- `tests/unit/`

Done criteria:

- Existing local ingest/standardize/embed path behavior is covered by tests.
- `BackfillPipeline.run_all()` works for multiple backfill-capable sources.
- Tests run without network or cloud credentials.

## Phase 2: Data Root Configuration

Objective:

Add a safe configurable data root for local and Quartz/Slate runs without changing the relative record layout.

Implementation instructions:

- Support a data root override through config and the canonical environment variable `EML_DATA_ROOT`.
- Preserve current local behavior when no override is set.
- Support environment-variable expansion such as `$USER` and `${USER}` in path config values.
- Define precedence explicitly: local defaults use `storage.base_dir: data` plus `paths.root: .`; `EML_DATA_ROOT` should replace the effective local data root rather than being appended under `data`.
- Avoid nested output paths such as `data/data/...` or `$EML_DATA_ROOT/data/...` unless a user explicitly configures that layout.
- Make root resolution explicit and testable.
- Keep existing dedupe and checkpoint files readable.
- Do not migrate or rename historical records.

Likely files:

- `src/eml_transformer/runtime.py`
- `src/eml_transformer/utils/config.py`
- `src/eml_transformer/storage/paths.py`
- `src/eml_transformer/storage/storage.py`
- `configs/dev.yaml`
- `configs/*.example.yaml`
- `tests/unit/`

Done criteria:

- Default local paths still resolve as before.
- `EML_DATA_ROOT=/some/slate/path` maps outputs directly to `/some/slate/path/bronze`, `/some/slate/path/silver`, `/some/slate/path/gold`, and `/some/slate/path/metadata`.
- Tests cover environment expansion and local defaults.

## Phase 3: Configuration Templates And Secret Hygiene

Objective:

Prepare shareable config patterns for local, Quartz-only, transfer orchestration, and production runs without committing private values.

Implementation instructions:

- Add `*.example.yaml` templates for:
  - local development
  - Quartz/Slate processing
  - AWS sandbox retrieval
  - short-lived S3 handoff with retention, explicit cleanup, and lifecycle safety-net settings
  - SDA archival
  - full workflow orchestration
- Keep user-specific config files as `*.local.yaml`.
- Update `.gitignore` for local configs, transfer logs, run metadata, archives, checksum manifests, temporary transfer state, and cloud/HPC credential artifacts.
- Document each new config field in comments or docs.
- Do not commit real bucket names, prefixes, profiles, SDA paths, Globus endpoint IDs, allocation names, or emails.

Likely files:

- `.gitignore`
- `configs/*.example.yaml`
- `docs/`

Done criteria:

- A user can copy example configs to local configs and fill placeholders.
- No template contains private values.
- Ignored patterns protect common cloud/HPC local artifacts.
- S3 handoff examples distinguish workflow cleanup commands from lifecycle safety-net settings.

## Phase 4: Historical Data Staging, Partitioning, And Aggregation

Objective:

Make historical downloads, especially GDELT history, partitioned and resumable before connecting them to transfer and orchestration commands.

Implementation instructions:

- Add a source-agnostic retrieval/handoff design for historical external data under configured Slate working storage.
- Implement GDELT history planning as a manifest of 15-minute GKG timestamps for a requested date range.
- For each GDELT timestamp, track expected remote URL, partition path, local Slate path, optional temporary S3 handoff URI, checksum when available, download status, raw row count, filtered row count, retention/cleanup status, and errors.
- Support partitioned download by date, month, timestamp shard, or SLURM array index.
- Keep raw GDELT files through validation. Archive raw files or explicit raw-file package manifests to SDA only when rerunning future filters without re-download is a requirement and storage budget allows it.
- Filter GDELT partitions independently using the existing configured themes, organizations, locations, and minimum-match logic.
- Write filtered partition outputs as Parquet under date partitions.
- Aggregate filtered partitions into daily or monthly bounded artifacts for ingestion handoff.
- Do not load a full year or multi-year history into memory at once.
- Add idempotent skip behavior for already verified partitions.
- Add `--force` or equivalent configuration only for explicit redownload/rebuild behavior.
- Design GDELT ingestion to accept source-native records from `live_http` or local/staged partition origins without changing standardization, scraping, or embedding. Any temporary S3 handoff origin must be transferred to Slate/local partitions before ingestion.
- Keep the existing Bronze/Silver/Gold layout as the downstream contract.
- Add a local sample mode using a tiny GDELT timestamp/date fixture or mocked downloader.

Likely files:

- `src/eml_transformer/acquisition/`
- `src/eml_transformer/ingestion/sources/gdelt.py`
- `src/eml_transformer/transfer/` if staging reuses transfer planners
- `src/eml_transformer/orchestration/` for staging manifests or step planning if needed
- `src/eml_transformer/storage/paths.py` only if partition path helpers are needed
- `configs/*.example.yaml`
- `tests/unit/`

Done criteria:

- A GDELT date range can be converted to a deterministic partition manifest.
- Partition paths are stable and date-indexed.
- Filtered daily aggregation is tested without live network access.
- Tests prove temporary S3 handoff metadata can be recorded in manifests without requiring ingestion code to read from S3.
- Existing downstream standardization/scraping/embedding behavior remains unchanged.

## Phase 5: Transfer Command Construction

Objective:

Add testable transfer modules for moving data from short-lived S3 handoff prefixes to Slate and from Slate to SDA without embedding transfer behavior into core processing code.

Implementation instructions:

- Add transfer command construction under `src/eml_transformer/transfer/`.
- Implement AWS S3 transfer planning with AWS CLI `aws s3 sync` as the default short-lived S3 handoff to Slate mechanism.
- Include dry-run support using AWS CLI `--dryrun`.
- Do not include `--delete` in S3 sync commands by default. If destructive mirroring is ever needed, require an explicit option, a tightly scoped destination, and tests.
- Prefer `--exact-timestamps` for S3-to-local syncs when timestamp fidelity matters for idempotency or audits.
- Add readiness checks for AWS CLI presence, AWS identity, configured profile/region when provided, S3 handoff prefix access when selected, and S3 retention/cleanup configuration.
- Add explicit cleanup command construction for temporary S3 handoff prefixes, separate from sync command construction.
- Add an SDA backend abstraction.
- Add Globus command construction only when configured.
- Add Globus dry-run support and checksum-oriented options when supported by the configured collections.
- Add filesystem or mounted-path backend only as a configured option.
- Add manual handoff backend that writes clear instructions and run metadata.
- Keep DataSync as a disabled optional advanced backend.
- Separate command construction from command execution.
- Log commands in a redacted, auditable form.

Likely files:

- `src/eml_transformer/transfer/`
- `src/eml_transformer/orchestration/` for readiness interfaces only if needed
- `tests/unit/`

Done criteria:

- Unit tests verify command lists without executing external CLIs.
- Dry-run command construction is covered.
- Tests verify S3 sync does not include destructive deletion flags unless explicitly requested.
- Missing CLI/credential/config errors are actionable.
- No production credentials or endpoints are hard-coded.

## Phase 6: Manifest, Checksums, And Archive Packaging

Objective:

Add archive-ready package creation and verification for processed outputs and reproducibility metadata.

Implementation instructions:

- Add manifest generation under `src/eml_transformer/archive/`.
- Include relative path, size, modification time, SHA-256 checksum, logical stage, run ID, timestamp, temporary source S3 handoff URI when applicable, Slate working path when applicable, SDA archive destination when applicable, cleanup status when applicable, and git commit hash when available.
- Include compatibility-critical metadata such as dedupe state and checkpoints.
- Implement checksum verification from a manifest.
- Implement tar and tar.gz package creation.
- Prefer uncompressed tar as the safe default for large or already-compressed datasets.
- Archive names should include project name, run ID, timestamp, and optionally git commit hash or dataset label.
- Do not overwrite existing archives unless explicitly configured.
- Keep archive generation rerunnable and auditable.

Likely files:

- `src/eml_transformer/archive/`
- `src/eml_transformer/storage/paths.py` only if archive paths need central helpers
- `tests/unit/`

Done criteria:

- Manifest generation and checksum verification are tested on a small fixture dataset.
- Archive no-overwrite behavior is tested.
- Archive creation tests do not require SDA access.

## Phase 7: Orchestration, Run State, And Readiness Checks

Objective:

Coordinate the workflow without duplicating processing behavior.

Implementation instructions:

- Add orchestration under `src/eml_transformer/orchestration/`.
- Orchestration should call existing CLI or internal pipeline functions instead of reimplementing ingestion, standardization, scraping, embedding, or backfill.
- Model the workflow as independent steps:
  - historical/source staging when configured
  - AWS sandbox retrieval when configured
  - short-lived S3 handoff validation
  - S3 handoff to Slate transfer
  - ingestion
  - standardization
  - scraping when configured outputs require article text
  - embedding
  - manifest generation
  - archive packaging
  - SDA transfer
  - explicit temporary S3/Slate cleanup
- Add non-mutating readiness checks for:
  - host context
  - compute versus login node when detectable
  - writable Slate paths
  - Python dependencies
  - AWS CLI and identity when S3 is selected
  - S3 handoff prefix access and retention/cleanup config when selected
  - Globus CLI and authentication when Globus is selected
  - SDA destination config
  - local space or quota when detectable
- Add run metadata records with run ID, timestamp, git commit hash when available, resolved config, commands, logs, transfer summaries, manifest path, archive checksum, Globus task ID when applicable, and success/failure status.
- Failed runs should leave enough information for diagnosis and safe retry.
- Do not rely on the current CLI `run-all` for production GDELT article-text workflows, because it does not call scraping. Use explicit step orchestration unless `run-all` behavior is intentionally changed and tested.
- GDELT history orchestration should be able to run staging, partition filtering, aggregation, ingestion, standardization, scraping, and embedding as separate resumable steps.

Likely files:

- `src/eml_transformer/orchestration/`
- `src/eml_transformer/cli.py`
- `tests/unit/`

Done criteria:

- A dry-run workflow can resolve config, validate readiness, and print/log planned steps without mutating data.
- Run state is written for dry runs and real runs where appropriate.
- Normal tests mock all external commands.

## Phase 8: CLI Surface For Transfer, Archive, And Workflow

Objective:

Expose the new transfer, archive, readiness, and workflow functions through the existing Typer CLI in a way that preserves current commands.

Implementation instructions:

- Keep existing commands working.
- Do not change existing `run-all` semantics unless that behavior change is explicitly requested and covered by tests.
- Add subcommands or command groups for:
  - `stage` or `history`
  - `transfer`
  - `archive`
  - `workflow`
  - readiness or config inspection
- Preserve the existing default config path unless explicitly changed.
- Support dry-run options on transfer and workflow commands.
- Support a dry-run for GDELT history retrieval/handoff that prints the timestamp partitions, expected local Slate paths, temporary S3 handoff paths, and cleanup plan without downloading.
- Make CLI errors actionable and user-facing.
- Avoid running heavy production workflows directly from a login-node shell; CLI commands can prepare or submit SLURM jobs.

Likely files:

- `src/eml_transformer/cli.py`
- `src/eml_transformer/orchestration/`
- `src/eml_transformer/transfer/`
- `src/eml_transformer/archive/`
- `tests/unit/`

Done criteria:

- Existing CLI command behavior remains covered.
- New CLI commands are tested with Typer's testing utilities or equivalent command invocation tests.
- Dry-run command paths do not execute cloud or IU transfers.

## Phase 9: SLURM Script Templates

Objective:

Replace user-specific operational scripts with safe, placeholder-based SLURM templates.

Implementation instructions:

- Add or update scripts for:
  - environment/setup checks
  - historical GDELT staging by date or SLURM array partition
  - AWS sandbox retrieval
  - S3 handoff to Slate transfer
  - processing
  - manifest/package creation
  - SDA transfer
  - temporary S3/Slate cleanup
  - full dependency-based chained submission
- Use `set -euo pipefail`.
- Use placeholders for account, partition, time, memory, CPUs, GPUs, email, project path, data root, and logs root.
- Write logs under a configurable Slate logs directory.
- Use `afterok` dependencies for chained submission.
- Keep GPU processing separate or behind an explicit switch.
- Avoid self-resubmitting jobs unless clearly documented and explicitly requested.

Likely files:

- `scripts/`
- `docs/`

Done criteria:

- Scripts contain placeholders, not private IU values.
- Script examples do not perform heavy work on login nodes.
- Chained submission uses `afterok`.

## Phase 10: User Documentation

Objective:

Document the complete workflow for humans without claiming unverified IU-specific details.

Implementation instructions:

- Document the AWS sandbox retrieval -> short-lived S3 handoff -> Quartz/Slate processing -> SDA archive workflow.
- Explain why Slate is temporary active working storage for fast retrieval by AI models and data-processing jobs.
- Explain why S3 is only a temporary retrieval handoff layer with explicit workflow cleanup, lifecycle backup cleanup, and target cleanup within 24 hours.
- Explain why SDA is the long-term archive and system of record.
- Explain why AWS CLI `s3 sync` is the default S3 handoff transfer method.
- Explain why Globus is preferred for SDA when configured and available.
- Explain why DataSync is optional and institution-dependent.
- Include setup, config, dry-run, SLURM submission, dependency chaining, resume/retry, manifest verification, archive creation, and troubleshooting instructions.
- Document current GDELT live HTTP/HTTPS history retrieval and the preferred partitioned staging path for production history.
- Document partition naming, aggregation levels, and how staged GDELT data feeds the existing pipeline seamlessly.
- Include a clear "Ask IU RT for this information" section for Globus endpoint IDs, SDA destination paths, quotas, transfer nodes, allocation names, and any DataSync route.
- Keep commands generic and placeholder-based.

Likely files:

- `docs/`
- `README.md` only for high-level links

Done criteria:

- A new user can follow docs using placeholders and local dry runs.
- Docs do not assert unconfirmed IU-specific endpoint IDs, routes, mounts, quotas, or limits.

## Phase 11: Optional Infrastructure Templates

Objective:

Provide optional AWS infrastructure examples only if needed. The main pipeline must work with existing buckets.

Implementation instructions:

- Do not require Terraform, CDK, DataSync, or new AWS resources for the main workflow.
- If adding S3 examples, include encryption, block public access, lifecycle cleanup for incomplete multipart uploads, least-privilege IAM scoped to specific handoff buckets/prefixes, explicit workflow cleanup commands, and lifecycle expiration for handoff objects as a backup.
- If adding S3 examples for GDELT history, use date-partitioned short-lived handoff prefixes and do not model S3 as the long-term location for raw, filtered, or processed outputs.
- Provide separate read-input and write-output policies when useful.
- Do not include credentials, private account IDs, bucket names, or personal profiles.
- Keep CloudWatch, CloudTrail, lifecycle transitions, and DataSync as optional advanced features.
- Keep DataSync disabled unless the user explicitly configures an existing IU-supported path or task.

Likely files:

- `infra/`
- `docs/`
- `tests/aws_contract/` if infrastructure templates become tracked code

Done criteria:

- Infrastructure examples are optional and secure by default.
- The core local and Quartz/Slate workflow does not depend on infrastructure creation.

## Phase 12: Manual Integration Test Playbooks

Objective:

Document live tests that require real AWS, Globus, SDA, IU accounts, or network access, while keeping automated tests offline-safe.

Implementation instructions:

- Add manual test docs for:
  - GDELT history partition manifest generation
  - small GDELT history retrieval/handoff dry run
  - AWS CLI identity and S3 read access
  - S3 handoff to Slate dry run
  - S3 handoff cleanup dry run
  - Globus authentication check
  - Globus transfer dry run
  - manifest verification on real outputs
  - archive handoff to SDA
  - full SLURM dependency chain on Quartz
- Mark all live integration tests as skipped by default if implemented in pytest.
- Document expected environment variables and placeholders.
- Include cleanup instructions.

Likely files:

- `docs/`
- `tests/integration/` only for skipped/manual tests

Done criteria:

- Normal `pytest` runs stay offline-safe.
- Manual test steps are clear enough to run on Quartz with configured credentials.
