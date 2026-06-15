# Project Structure

This document explains the top-level folder structure of the project and the
purpose of each major directory.

## Overview

The repository separates code, configuration, data, documentation, notebooks,
scripts, infrastructure, and tests.

```text
project/
+-- configs/
+-- data/
+-- docs/
+-- infra/
+-- notebooks/
+-- scripts/
+-- src/
+-- tests/
```

## `configs/`

The `configs/` folder stores configuration files that control project behavior
without requiring source code changes.

Configuration files define:

- enabled data sources
- source parameters
- storage locations
- deployment settings
- runtime service limits
- preprocessing and embedding settings

Important subdirectories:

```text
configs/
+-- deployments/
+-- environments/
+-- generated/
+-- sources/
```

Generated runtime configs under `configs/generated/` are local artifacts and
should not be committed.

## `data/`

The `data/` folder stores local project outputs. Local runs normally use
`data/` as the root. AWS deployment configs currently use `paths.root: .`, so
the same generic medallion keys start at `bronze/`, `silver/`, `gold/`, and
`metadata/` in S3.

See `docs/aws_s3_layout.md` for the full AWS object layout, including GDELT,
article fetch, manifests, restore staging, and lifecycle-managed prefixes.

```text
data/
+-- bronze/
+-- silver/
+-- gold/
+-- metadata/
```

### `data/bronze/`

The `bronze/` folder stores raw records collected from each generic source.
These files preserve the original source output as closely as possible.

Examples:

```text
data/bronze/source=miso_notifications/records.jsonl
data/bronze/source=weather_alerts/records.jsonl
```

In S3, generic source appends can be stored under
`bronze/source=<source>/records.jsonl.parts/`; the S3 reader combines the marker
object and all part files.

### `data/silver/`

The `silver/` folder stores standardized records after they have been parsed
into a shared schema.

Examples:

```text
data/silver/source=miso_notifications/records.parquet
data/silver/source=weather_alerts/records.parquet
```

### `data/gold/`

The `gold/` folder stores model-ready outputs such as embeddings.

Example:

```text
data/gold/model=nvidia-nv-embedqa-e5-v5/source=weather_alerts/embeddings.parquet
```

### `data/metadata/`

The `metadata/` folder stores processing state for generic sources.

```text
data/metadata/dedupe/source=<source>.json
data/metadata/checkpoint/source=<source>.json
```

This supports incremental updates and prevents repeated records from being
written multiple times.

## `docs/`

The `docs/` folder stores project documentation.

```text
docs/
+-- architecture.md
+-- aws_s3_layout.md
+-- ingestion_pipeline.md
+-- local_setup.md
+-- project_structure.md
```

Use this folder for:

- project goals
- architecture decisions
- pipeline behavior
- storage contracts
- operator runbooks
- design notes

## `infra/`

The `infra/` folder contains infrastructure-as-code.

```text
infra/
+-- cdk/
```

CDK is the AWS deployment path for collection infrastructure.

## `notebooks/`

The `notebooks/` folder stores Jupyter notebooks used for exploration,
debugging, and quick analysis.

Notebooks are useful for:

- inspecting outputs
- testing ideas quickly
- debugging data issues
- visualizing intermediate results

They should be used for exploration rather than core reusable code.

## `scripts/`

The `scripts/` folder stores standalone utility scripts and operational smoke
tests.

Examples include:

- backfilling a source
- running AWS smoke phases
- testing a temporary workflow
- performing maintenance tasks

## `src/`

The `src/` folder contains the main Python package code.

```text
src/
+-- eml_transformer/
```

Keeping source code inside `src/` helps the project behave like a proper
installable Python package and keeps runtime logic separate from data,
notebooks, configs, and docs.

Major areas include:

- acquisition
- cloud runtime integration
- command-line execution
- deployment config helpers
- ingestion source interfaces
- pipelines
- storage helpers
- text processing
- utilities

## `tests/`

The `tests/` folder stores automated tests for the project.

Tests verify:

- source parsing
- schema validation
- text cleaning
- deduplication
- config loading
- storage paths
- AWS runtime behavior

## Root-Level Files

Important root-level files include:

- `README.md`: high-level introduction, setup, and basic usage.
- `pyproject.toml`: package metadata, dependencies, and CLI entry points.
- `requirements.txt`: compatibility install file for the AWS runtime extra.
- `MakeFile`: shortcuts for common development commands.

## Summary

```text
configs/    -> runtime and deployment settings
data/       -> local stored project data
docs/       -> developer and operator documentation
infra/      -> infrastructure-as-code
notebooks/  -> exploration and debugging
scripts/    -> operational and utility scripts
src/        -> reusable package code
tests/      -> automated tests
```
