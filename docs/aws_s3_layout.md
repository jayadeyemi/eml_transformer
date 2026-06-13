# AWS S3 Data Layout

This document is the storage contract for AWS-backed runs. The code paths are
implemented in `src/eml_transformer/storage/paths.py`,
`src/eml_transformer/pipelines/*`, `src/eml_transformer/acquisition/gdelt/`,
and `src/eml_transformer/cloud/aws/runtime.py`.

## Prefix Rules

All S3 keys are relative to the configured data bucket.

```yaml
storage:
  backend: s3
  bucket: <bucket-name>
  prefix: <optional-environment-prefix>

paths:
  root: .
```

`storage.prefix` is applied by `S3Storage` to every object key. If the prefix is
`smoke`, the application key `bronze/source=weather_alerts/records.jsonl`
becomes `s3://<bucket>/smoke/bronze/source=weather_alerts/records.jsonl`.

`paths.root` is applied only to the generic medallion pipeline paths. Current
AWS deployment configs use `paths.root: .`, so generic pipeline keys start at
`bronze/`, `silver/`, `gold/`, and `metadata/`. If a deployment sets
`paths.root: data`, those same keys move under `data/`.

## Generic Source Pipeline

These paths are used by registry-backed sources such as `weather_alerts`,
`miso_notifications`, `iem_afos`, and `newsapi`.

```text
<root>/bronze/source=<source>/records.jsonl
<root>/bronze/source=<source>/records.jsonl.parts/<uuid>.jsonl
<root>/metadata/dedupe/source=<source>.json
<root>/metadata/checkpoint/source=<source>.json
<root>/silver/source=<source>/records.parquet
<root>/gold/model=<model>/source=<source>/embeddings.parquet
```

With the current AWS root of `.`, the concrete keys are:

```text
bronze/source=weather_alerts/records.jsonl
bronze/source=weather_alerts/records.jsonl.parts/<uuid>.jsonl
metadata/dedupe/source=weather_alerts.json
metadata/checkpoint/source=weather_alerts.json
silver/source=weather_alerts/records.parquet
gold/model=nvidia-nv-embedqa-e5-v5/source=weather_alerts/embeddings.parquet
```

Local storage appends bronze rows directly to `records.jsonl`. S3 storage uses
append-safe part files: `records.jsonl` is a marker object, and appended rows
are written under `records.jsonl.parts/`. `S3Storage.read_jsonl()` reads the
marker object plus all part files, so standardization retrieves the complete
bronze dataset for the source.

Standardization overwrites one silver parquet object per source. Embedding reads
the silver parquet object and overwrites one gold parquet object per
`(model, source)`.

## GDELT Discovery

GDELT discovery does not use the generic `StoragePaths` medallion keys. It uses
file-level GDELT keys so that 15-minute GKG files can be cached and re-used
across discovery runs.

```text
bronze/gdelt/raw/table=gkg/date=<yyyy-mm-dd>/timestamp=<yyyymmddhhmmss>/<timestamp>.gkg.csv.zip
bronze/gdelt/candidate_urls/table=gkg/date=<yyyy-mm-dd>/timestamp=<yyyymmddhhmmss>/parser_version=<parser>/filter_version=<filter>/candidate_urls.jsonl
manifests/gdelt_files/table=gkg/date=<yyyy-mm-dd>/timestamp=<yyyymmddhhmmss>/parser_version=<parser>/filter_version=<filter>.json
manifests/runs/run_id=<run_id>/gdelt_discovery.json
```

The candidate URL key is the input to URL enqueueing and article fetch. GDELT
currently does not produce `silver/source=gdelt/records.parquet`; downstream
standardization for GDELT article text is still separate from the generic source
pipeline.

## Article Fetch Output

Article fetch workers write fetched article payloads under `bronze/articles/`.
The current worker path normally writes batch JSONL objects and records the
batch object key in DynamoDB URL state.

```text
bronze/articles/batches/fetch_date=<yyyy-mm-dd>/run_id=<run_id>/<uuid>.jsonl
bronze/articles/batches/fetch_date=<yyyy-mm-dd>/run_id=<run_id>/<uuid>.jsonl.gz
```

The single-payload writer uses this path when called directly:

```text
bronze/articles/source=<article_source>/source_domain=<domain>/fetch_date=<yyyy-mm-dd>/<url_hash>.json
```

## Restore And Temporary Prefixes

S3 archive restore operations use the source object key by default and can copy
temporary inspection data under:

```text
restore-staging/
tmp/
```

The CDK stack applies lifecycle cleanup to `restore-staging/` and `tmp/`, and
archive transitions to `bronze/`. It does not currently archive `silver/`,
`gold/`, `metadata/`, or `manifests/`.

## Operator Checks

Use these checks after a smoke or dev run to confirm data landed in the expected
folders:

```bash
aws s3 ls "s3://<bucket>/bronze/source=weather_alerts/" --recursive --profile <profile>
aws s3 ls "s3://<bucket>/bronze/source=weather_alerts/records.jsonl.parts/" --recursive --profile <profile>
aws s3 ls "s3://<bucket>/silver/source=weather_alerts/" --recursive --profile <profile>
aws s3 ls "s3://<bucket>/bronze/gdelt/candidate_urls/" --recursive --profile <profile>
aws s3 ls "s3://<bucket>/bronze/articles/batches/" --recursive --profile <profile>
aws s3 ls "s3://<bucket>/manifests/runs/" --recursive --profile <profile>
```

If `storage.prefix` is configured, insert it between the bucket and the folder,
for example `s3://<bucket>/<prefix>/bronze/...`.

## Current Gaps

- Generic sources have dedupe and checkpoint files, but no run-window manifest
  keyed by `(source, checkpoint_value)`. Re-running the same window can still
  call the upstream source even when all records are deduped locally.
- There is no automated integration test that asserts the full S3 folder
  contract against LocalStack or a smoke bucket.
- `config-render-from-outputs` renders live AWS resources only. Operators must
  merge or preserve source configuration when using that generated file for
  local smoke commands.
- GDELT parsing still materializes compressed GKG content before parsing. Large
  daily runs need the planned streaming parser.
- GDELT article fetch output is not yet standardized into the generic
  `silver/source=<source>/records.parquet` layout.
