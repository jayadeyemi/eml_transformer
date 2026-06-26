# AGENTS.md

## Tests

This directory contains tests for configuration, paths, transfer command construction, manifests, archives, readiness checks, and workflow orchestration.

## Rules

- Normal tests must not require AWS, Globus, SDA, IU accounts, live credentials, or network access.
- Mock external commands.
- Mock cloud, Globus, SDA, filesystem transfer backend, and SLURM interactions.
- Add fixtures for a small local dataset.
- Test environment-variable expansion.
- Test that local development defaults still work.
- Test that `EML_DATA_ROOT` changes the data root without changing relative record layout.
- Test manifest generation and checksum verification.
- Test archive naming and no-overwrite behavior.
- Test dry-run command construction.
- Test that S3 handoff configs include retention/cleanup behavior and do not model S3 as durable storage.
- Test that S3 sync command construction avoids destructive deletion flags unless an explicit destructive option is set.
- Test orchestration readiness checks as non-mutating checks.
- Integration tests requiring real AWS, Globus, or SDA access should be documented and skipped by default.
- Prototype branch tests under `tests/unit`, `tests/contract`, and `tests/aws_contract` are useful references for coverage areas, but recreate tests as source files, not generated cache artifacts, and keep normal tests offline-safe.
