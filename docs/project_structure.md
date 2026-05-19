# Project Structure

This document explains the top-level folder structure of the project and the purpose of each major directory.

## Overview

The repository is organized to separate code, configuration, data, documentation, notebooks, scripts, and tests.

```text
project/
├── configs/
├── data/
├── docs/
├── notebooks/
├── scripts/
├── src/
└── tests/
```

## `configs/`

The `configs/` folder stores configuration files that control project behavior without requiring changes to the source code.

For example:

```text
configs/
└── ingestion.yaml
```

Configuration files are useful for storing settings such as:
- enabled data sources
- source parameters
- storage locations
- run options
- preprocessing settings

Using configuration files makes experiments easier to repeat because the settings for a run can be saved separately from the code.

## `data/`

The `data/` folder stores project data outputs.

It is organized by processing level and metadata.

```text
data/
├── bronze/
├── silver/
└── metadata/
```

### `data/bronze/`

The `bronze/` folder stores raw records collected from each source.

These files preserve the original source output as closely as possible.

Example:

```text
data/bronze/source=miso_notifications/records.jsonl
data/bronze/source=weather_alerts/records.jsonl
```

### `data/silver/`

The `silver/` folder stores standardized records after they have been parsed into a shared format.

Example:

```text
data/silver/text_records/source=miso_notifications/records.csv
data/silver/text_records/source=weather_alerts/records.csv
```

### `data/metadata/`

The `metadata/` folder stores information needed to track processing state.

For example, the deduplication files help keep track of which records have already been seen.

```text
data/metadata/dedupe/
```

This supports incremental updates and prevents repeated records from being written multiple times.

## `docs/`

The `docs/` folder stores project documentation.

Example:

```text
docs/
├── architecture.md
├── ingestion_pipeline.md
├── project_goals.pdf
└── sample_prompts.md
```

This folder is used to explain:
- project goals
- architecture decisions
- pipeline logic
- sample prompts
- design notes

Keeping documentation in the repository makes the project easier to understand, maintain, and share.

## `notebooks/`

The `notebooks/` folder stores Jupyter notebooks used for exploration, debugging, and quick analysis.

Example:

```text
notebooks/
└── DEBUG.ipynb
```

Notebooks are useful for:
- inspecting outputs
- testing ideas quickly
- debugging data issues
- visualizing intermediate results

They should generally be used for exploration rather than core reusable code.

## `scripts/`

The `scripts/` folder stores standalone utility scripts.

Example:

```text
scripts/
└── backfill_newsapi.py
```

Scripts are useful for one-off tasks or operational commands that do not need to be part of the main package interface.

Examples include:
- backfilling a source
- running a special data pull
- testing a temporary workflow
- performing maintenance tasks

## `src/`

The `src/` folder contains the main Python package code.

Example:

```text
src/
└── eml_transformer/
```

This is where reusable project logic lives.

Keeping source code inside `src/` helps make the project behave like a proper installable Python package. It also keeps code separate from data, notebooks, configs, and documentation.

The package contains modules for:
- command-line execution
- ingestion logic
- storage helpers
- text processing
- utilities
- future modeling code

## `src/eml_transformer/`

This is the main project package.

```text
src/eml_transformer/
```

It contains the reusable Python code used across the project.

Major areas include:
- ingestion
- pipelines
- storage
- text processing
- utilities
- models

## `src/eml_transformer/ingestion/`

This folder contains code related to collecting and standardizing data from external sources.

It includes shared ingestion interfaces, schemas, source registration, and source-specific implementations.

## `src/eml_transformer/storage/`

This folder contains code for managing storage paths and reading or writing outputs.

It helps centralize file handling so paths and storage behavior are not scattered throughout the codebase.

## `src/eml_transformer/text_processing/`

This folder contains reusable text cleaning and validation logic.

Examples include:
- whitespace cleanup
- HTML cleaning
- text validation
- basic formatting checks

Keeping this separate makes it easier to reuse the same text processing logic across sources.

## `src/eml_transformer/utils/`

This folder contains general helper code used across the project.

Examples include:
- configuration loading
- stable hashing
- timestamp utilities
- reusable support functions

## `src/eml_transformer/models/`

This folder is reserved for future modeling-related code.

It can later contain logic for:
- embedding generation
- model training
- model evaluation
- forecasting models

## `src/eml_transformer/pipelines/`

This folder contains higher-level orchestration code.

Pipeline modules are responsible for connecting multiple pieces of the project together into a repeatable workflow.

## `tests/`

The `tests/` folder stores automated tests for the project.

Tests are used to verify that important parts of the code behave correctly.

Examples of what can be tested:
- source parsing
- schema validation
- text cleaning
- deduplication
- config loading
- storage paths

Adding tests makes the project safer to modify as it grows.

## Root-Level Files

The project also contains several important root-level files.

### `README.md`

The README provides a high-level introduction to the project.

It usually includes:
- project purpose
- setup instructions
- basic usage
- repository overview

### `pyproject.toml`

This file defines Python package settings.

It can include:
- package metadata
- dependencies
- command-line entry points
- build settings

### `requirements.txt`

This file lists Python dependencies needed to run the project.

### `MakeFile`

The MakeFile provides shortcuts for common development commands.

Examples may include:
- installing dependencies
- running tests
- formatting code
- running ingestion commands


## Summary

The repository is organized around clear responsibilities:

```text
configs/    -> runtime settings
data/       -> stored project data
docs/       -> documentation
notebooks/  -> exploration and debugging
scripts/    -> standalone utility scripts
src/        -> reusable package code
tests/      -> automated tests
```

This structure keeps the project easier to understand, easier to extend, and easier to maintain.