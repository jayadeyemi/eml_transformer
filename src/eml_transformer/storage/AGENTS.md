# AGENTS.md

## Storage Compatibility

This directory owns storage abstractions and path compatibility. Be especially conservative here.

## Current Layout

- Local defaults currently combine `storage.base_dir: data` with `StoragePaths.root="."`, producing paths under `data/`.
- Preserve these relative paths:
  - `bronze/source=<source>/records.jsonl`
  - `silver/source=<source>/<artifact>.parquet`
  - `gold/model=<model>/source=<source>/embeddings.parquet`
  - `metadata/dedupe/source=<source>.json`
  - `metadata/checkpoint/source=<source>.json`

## Rules

- Do not break the current Bronze/Silver/Gold/metadata layout.
- Do not rename existing record locations unless a documented migration is added.
- Keep existing local storage behavior working.
- Prefer making the base data root configurable rather than changing relative paths.
- `EML_DATA_ROOT` should be the canonical environment override for the effective data root. It must replace the local data root, not be appended under `storage.base_dir: data`, and must not create nested paths such as `data/data/...`.
- Existing dedupe hash tables and checkpoint files must remain readable.
- Do not turn AWS S3 or SDA into the active processing filesystem by default.
- Treat the existing `S3Storage` as experimental for the Quartz/Slate workflow unless dependencies, JSONL read/append behavior, tests, and semantics are completed later.
- S3 storage support, if extended, must not be confused with the preferred production design: AWS sandbox retrieval writes short-lived S3 handoff objects, Quartz transfers data to temporary Slate working storage, processing reads/writes on Slate, and final verified records are archived to SDA.
- Do not place transfer aggregation or S3 sync orchestration in storage by default. Prototype branch code such as `storage/transfer.py` may inform tests and data-shape ideas, but transfer planning belongs in `transfer`, and historical partition aggregation belongs in `acquisition` or `archive` depending on purpose.
