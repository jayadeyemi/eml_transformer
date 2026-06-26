# AGENTS.md

## Acquisition Boundary

This directory owns external dataset discovery and historical staging before records enter the existing ingestion pipeline. It is especially appropriate for GDELT history planning, timestamp manifests, raw-file caching, partition filtering, and aggregation manifests.

## Rules

- Do not put source standardization, scraping, embedding, archive packaging, or cloud deployment logic here.
- Preserve the existing ingestion pipeline as the handoff boundary: acquisition should produce source-native records, staged partitions, or manifests that ingestion can consume.
- For GDELT, prefer deterministic timestamp manifests, raw ZIP retention while filtering, optional raw-file archive manifests, date-partitioned filtered Parquet, and daily/monthly bounded aggregation.
- Do not load full-year or multi-year GDELT history into memory.
- Support dry-run planning that lists expected remote URLs, local Slate paths, optional short-lived S3 handoff URIs, retention/cleanup expectations, and partition keys without downloading.
- Keep download, parse, filter, and aggregate steps separately testable.
- Do not hard-code GDELT filters; use config-driven themes, organizations, locations, parser versions, and filter versions.
- Do not store credentials or assume AWS/S3 is the active processing backend. AWS sandbox retrieval jobs may use S3 only as a temporary handoff layer, with explicit cleanup after transfer and target cleanup within 24 hours.
- If data arrives through S3 handoff, transfer it to configured Slate/local partitions before ingestion. Acquisition manifests may record S3 URIs for provenance, but processing code should consume local/staged source-native inputs.
- Normal tests must mock network downloads and use small fixture bytes or DataFrames.
