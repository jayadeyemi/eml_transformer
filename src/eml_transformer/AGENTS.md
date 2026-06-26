# AGENTS.md

## Package Code

This directory contains the application package. Preserve the existing package shape and CLI style. Current core areas include `ingestion`, `pipelines`, `storage`, `text_processing`, `extraction`, and `utils`.

## Rules

- Prefer small, well-contained modules over broad rewrites.
- Keep existing ingestion, standardization, scraping, embedding, storage path, dedupe, and checkpoint behavior compatible.
- Add historical external data discovery and staging under an acquisition-focused module when records are not yet ready for the existing ingestion pipeline.
- Add high-level workflow coordination under an orchestration-focused module.
- Add data movement code under a transfer-focused module.
- Add manifest, checksum, archive naming, and package creation code under archive-focused modules.
- Do not add AWS, Globus, SDA, DataSync, or SLURM assumptions inside core processing logic or source implementations.
- Do not make ingestion sources read directly from temporary S3 handoff locations. Transfer/acquisition code should materialize or describe local/staged source-native inputs before ingestion consumes them.
- Orchestration should call existing CLI or pipeline functions instead of duplicating scientific/data-processing behavior.
- The current CLI `run-all` command skips scraping; orchestration that needs article text should call the scraping pipeline explicitly.
- Use typed, testable functions.
- Keep external command construction isolated from command execution so it can be tested without cloud or IU services.
- Make errors actionable and user-facing, especially for missing tools, credentials, paths, permissions, and config values.
- Prototype branch modules named `cloud`, `deployment`, `commands`, and `services` should not be recreated by default. Fold their useful ideas into `transfer`, `orchestration`, `archive`, `acquisition`, `configs`, `scripts`, or `infra` according to responsibility.
