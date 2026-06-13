# Ingestion Pipeline

## Overview

The goal of the ingestion pipeline is to create a robust and reproducible framework for gathering, storing, and preprocessing textual data from multiple external sources for downstream modeling and analysis.

The pipeline is designed around a medallion-style architecture:

```text
Bronze -> Silver -> Gold
```

Each layer has an independent purpose:

- **Bronze** stores raw source data exactly as retrieved.
- **Silver** standardizes and cleans records into a consistent schema.
- **Gold** prepares modeling-ready datasets such as embeddings.

This separation improves:
- reproducibility
- debugging
- source traceability
- incremental updates
- downstream flexibility

# Pipeline Stages

## 1. Raw Ingestion (Bronze Layer)

The bronze layer is responsible for collecting raw data from multiple external sources and storing the original responses without modification.

To support many different providers consistently, the ingestion system uses a shared source abstraction. Each source implements the same high-level interface while handling its own API logic internally.

Examples of sources include:

- MISO notifications
- NewsAPI articles
- National Weather Service alerts

Even though these sources return different response formats, they all follow the same ingestion workflow:

```python
fetch_raw()
parse_records()
standardize_records()
```

This abstraction allows the pipeline to:
- reuse ingestion logic across sources
- simplify adding new data providers
- maintain a consistent downstream schema
- separate source-specific logic from pipeline orchestration

### Goals

- Preserve original source responses
- Support reproducibility and auditing
- Allow reprocessing without re-querying APIs
- Track ingestion timestamps and duplicate values
- Handle incremental updates
- Support modular multi-source ingestion

### Operations

```python
ingest_raw
  fetch API data
  write raw JSON/JSONL
  update duplicate values
```

### Bronze Storage

Raw responses are stored in:

```text
bronze/
```

Typical formats include:
- `.json`
- `.jsonl`

The bronze layer should remain append-only whenever possible so historical raw responses are preserved.

### Example Bronze Record

```json
{
  "source": "miso_notifications",
  "record_id": "53ef5ce06c06474ad3a88b23fca73a944c828ee736df61b68f57bd542c3df5ac",
  "content_hash": "f1ca4716c08f2d730a8a3369a190f53236ef17d89acb0b469712309cc68aee84",
  "dedupe_key": "53ef5ce06c06474ad3a88b23fca73a944c828ee736df61b68f57bd542c3df5ac:f1ca4716c08f2d730a8a3369a190f53236ef17d89acb0b469712309cc68aee84",
  "retrieved_at": "2026-05-19T17:20:29.696968+00:00",
  "run_id": "20260519T172029Z",
  "raw": {
    ...
  }
}
```

### Record Components

- `source`  
  Identifies which ingestion source produced the record.

- `record_id`  
  Stable identifier for the logical record.

- `content_hash`  
  Hash of the raw content used to detect changes in the source payload.

- `dedupe_key`  
  Combined identifier used for incremental ingestion and deduplication.

- `retrieved_at`  
  UTC timestamp indicating when the record was collected.

- `run_id`  
  Identifier for the ingestion pipeline execution.

- `raw`  
  Original unmodified source response.

## 2. Standardization (Silver Layer)

The silver layer converts heterogeneous source responses into a unified schema.

Different APIs return data in very different formats. The standardization stage ensures that all records can be processed consistently downstream.

### Goals

- Parse source-specific responses
- Normalize fields across sources
- Create a shared schema
- Perform lightweight text cleaning
- Remove malformed records
- Generate stable IDs for deduplication

### Operations

```python
standardize
  read raw
  parse records
  convert to TextRecord
  write silver CSV/Parquet
```

# Shared Text Schema

All standardized records are converted into a common `TextRecord` structure.

Example fields:

```python
TextRecord(
    record_id,
    source,
    source_type,
    title,
    text,
    published_at,
    retrieved_at,
    url,
    region,
    categories,
    raw
)
```

## Silver Storage

Standardized records are stored in:

```text
silver/
```

Formats may include:
- CSV
- Parquet

Records are partitioned by source but can also be further split by date

## Text Cleaning Strategy

Only lightweight and non-destructive preprocessing should occur in silver.

Examples:
- strip HTML
- normalize whitespace
- remove duplicated line breaks
- standardize encodings
- basic parsing cleanup

The goal is to preserve semantic meaning while improving consistency.

Recommended pattern:

```text
text_raw     -> original extracted text
text_clean   -> lightly cleaned text
```

## 3. Feature Engineering / Embeddings (Gold Layer)

The gold layer produces modeling-ready datasets.

This layer combines standardized text data with downstream feature engineering and embedding generation.

### Planned Goals

- Combine multiple text sources
- Chunk long documents
- Generate embeddings
- Create temporal aggregations
- Join with forecasting targets
- Produce model-ready datasets

### Example Operations

```python
gold_processing
  read silver data
  preprocess for embeddings
  generate embeddings
  combine sources
  aggregate features
  write gold datasets
```

## Gold Storage

Gold datasets are stored in:

```text
gold/
```

Potential formats:
- Parquet
- CSV
- vector databases
- embedding stores

Example outputs:
- embedding tables
- forecasting features
- aggregated NLP signals
- downstream ML datasets

## Incremental Updates

The ingestion framework is designed to support incremental ingestion.

Each source maintains:
- retrieval timestamps
- checkpoints
- deduplication logic
- stable record IDs

This allows the system to:
- avoid duplicate ingestion
- continue from previous runs
- support scheduled updates
- minimize API usage

## Deduplication

Stable record IDs are generated using deterministic hashing.

Example strategy:

```python
stable_hash(
    source +
    published_at +
    title +
    url
)
```

This ensures:
- identical records map to the same ID
- rerunning ingestion does not create duplicates
- records remain stable across runs



# High-Level Pipeline Flow

```text
External APIs
      |
      v
+----------------+
| Bronze Layer   |
| Raw JSON/JSONL |
+----------------+
      |
      v
+----------------+
| Silver Layer   |
| Standardized   |
| Text Records   |
+----------------+
      |
      v
+----------------+
| Gold Layer     |
| Embeddings &   |
| ML Features    |
+----------------+
```

## AWS CDK Option with Terraform Compatibility

The AWS option separates durable infrastructure ownership from runtime collection behavior.
AWS CDK in Python is the primary deployment path. Terraform is retained as a
secondary compatibility/reference path and should not manage the same deployed
environment as CDK unless resources are intentionally migrated.

- **CDK owns durable AWS resources**: S3 buckets, SQS queues, DynamoDB tables, IAM roles, ECR repositories, AWS Batch job definitions, Step Functions workflows, EventBridge schedules, CloudWatch alarms, cost controls, and optional AWS Budgets alerts.
- **Python AWS SDK runtime code uses existing resources**: it writes S3 objects, sends SQS messages, updates DynamoDB state, emits CloudWatch metrics, and starts Step Functions or Batch jobs.
- **The SDK must not create durable infrastructure** such as buckets, queues, tables, IAM roles, schedules, or state machines.

### Deployment Diagram

```text
Developer / CI
    |
    |  git push / pull request
    v
Repository
    |
    +--------------------------+
    |                          |
    | cdk synth/diff/deploy    | docker build/push
    v                          v
CloudFormation             Amazon ECR
    |                      collection container image
    |
    | creates durable AWS resources
    v
+--------------------------------------------------------------------+
| AWS Account                                                        |
|                                                                    |
|  EventBridge Scheduler                                             |
|        |                                                           |
|        v                                                           |
|  Step Functions acquisition workflow                               |
|        |                                                           |
|        v                                                           |
|  AWS Batch / Fargate job queue <--------- pulls image from ECR      |
|        |                                                           |
|        +--> service-run ingest / standardize / embed / backfill     |
|        |                                                           |
|        +--> gdelt-discover --> SQS URL fetch queue                  |
|                                  |                                 |
|                                  v                                 |
|                            article-fetch-worker                    |
|                                                                    |
|  S3 data lake: bronze / silver / gold / manifests                   |
|  DynamoDB: run state / URL state / domain throttle                  |
|  CloudWatch: logs / metrics / alarms                               |
|  IAM: execution roles and least-privilege access                    |
+--------------------------------------------------------------------+
```

### Collection Microservices

AWS runs the same pipeline stages as the local CLI by packaging the repository as one container image and selecting a service command at runtime.

Supported collection services:

- `ingest` collects bronze data for any registered source or all enabled sources.
- `standardize` converts bronze data into silver `TextRecord` tables.
- `embed` creates gold embeddings from silver data.
- `backfill` runs windowed historical ingestion for sources that support backfill.
- `run_all` runs ingestion, standardization, and embedding in sequence.
- `gdelt_discovery` handles high-volume GDELT file discovery and URL queueing.
- `url_fetch_worker` consumes queued URLs and stores fetched article payloads.

This keeps the cloud execution model aligned with the preexisting `IngestionPipeline`, `StandardizationPipeline`, `EmbeddingPipeline`, `BackfillPipeline`, source registry, and storage abstraction.

### Layered Deployment Config

AWS deployments use deterministic YAML layering:

```text
configs/base.yaml
configs/environments/<environment>.yaml
configs/sources/<source>.yaml
configs/deployments/<deployment>.yaml
```

Merge order is base, environment, selected source files, and finally the
deployment file. `infra.engine: cdk` is the default for AWS. Runtime values are
rendered with:

```bash
eml_transformer config-validate --deployment configs/deployments/aws-dev.yaml
eml_transformer deployment-matrix --deployment configs/deployments/aws-dev.yaml
eml_transformer config-render --deployment configs/deployments/aws-dev.yaml --output configs/generated/aws-dev.runtime.yaml
```

`configs/aws.example.yaml` intentionally contains fake `123456789012`
account/resource values for public documentation only. Real generated runtime
configs under `configs/generated/*.yaml` should not be committed; use CDK
outputs, environment variables, CI secrets, or ignored generated YAML for real
ARNs.

### AWS Security Defaults

- S3 data lake buckets block public access.
- SQS queues are not given public queue policies.
- Step Functions and Batch job resources are invoked through IAM permissions,
  not public invocation policies.
- Runtime containers receive temporary permissions through IAM roles, not
  long-lived AWS keys.
- CDK and Terraform must not manage the same live environment unless resources
  have been explicitly migrated/imported.

### AWS Acquisition Flow

```text
EventBridge Scheduler
      |
      v
Step Functions acquisition workflow
      |
      v
AWS Batch / Fargate: gdelt-discover
      |
      +--> S3 bronze/gdelt candidate URL manifests
      +--> DynamoDB URL state table
      +--> SQS URL fetch queue
               |
               v
        AWS Batch / Fargate: article-fetch-worker
               |
               +--> S3 bronze/articles raw HTML/text/metadata
               +--> DynamoDB fetch state updates
               +--> SQS DLQ for repeated failures
```

For smaller API sources such as MISO, NewsAPI, IEM AFOS, and Weather Alerts, AWS Batch can run the generic collection service directly:

```text
AWS Batch / Fargate: service-run --service ingest --source <source>
AWS Batch / Fargate: service-run --service standardize --source <source>
AWS Batch / Fargate: service-run --service embed --source <source>
AWS Batch / Fargate: service-run --service backfill --source <source>
```

### Storage Layout

The AWS data lake preserves the medallion model, but it has two namespaces:
generic registry sources use `StoragePaths`, while GDELT and article fetch use
specialized acquisition keys. See `docs/aws_s3_layout.md` for the authoritative
folder contract.

```text
s3://<bucket>/<prefix>/bronze/source=<source>/records.jsonl
s3://<bucket>/<prefix>/bronze/source=<source>/records.jsonl.parts/<uuid>.jsonl
s3://<bucket>/<prefix>/metadata/dedupe/source=<source>.json
s3://<bucket>/<prefix>/metadata/checkpoint/source=<source>.json
s3://<bucket>/<prefix>/silver/source=<source>/records.parquet
s3://<bucket>/<prefix>/gold/model=<model>/source=<source>/embeddings.parquet
s3://<bucket>/<prefix>/bronze/gdelt/
s3://<bucket>/<prefix>/bronze/articles/
s3://<bucket>/<prefix>/manifests/
```

`<prefix>` is omitted when `storage.prefix` is empty. Current AWS deployment
configs set `paths.root: .`, so generic paths do not include a leading
`data/` folder.

For S3 generic ingestion, `records.jsonl` can be a marker object and appended
rows can live under `records.jsonl.parts/`. The S3 reader loads both locations.

### Archive And Recovery

CDK applies archive lifecycle rules to `s3://<bucket>/bronze/` because
bronze raw data is the largest and easiest layer to regenerate from downstream
processing. The default lifecycle is:

```text
0-90 days       S3 Standard
90-365 days     S3 Glacier Instant Retrieval
>365 days       S3 Glacier Deep Archive
```

Silver, gold, and manifests are left out of the archive rule by default so
normal standardization, modeling, and audit reads do not need an archive restore
step. This can be changed later with additional lifecycle rules if storage cost
requires it.

Rollback relies on S3 versioning plus explicit restore operations:

```text
Deep Archive object
      |
      v
RestoreObject request
      |
      v
Temporary restored copy becomes readable
      |
      +--> copy to restore-staging/ for inspection
      |
      +--> copy over the same key as a new Standard current version
```

The second path is the rollback path. Because bucket versioning is enabled, the
copy creates a new current version while retaining the archived prior version in
history. For bulk recovery, use S3 Batch Operations with an inventory or CSV
manifest rather than restoring keys one at a time.

### CLI Commands

The collection services are exposed through CLI commands:

```bash
eml_transformer service-run --config configs/generated/aws-dev.runtime.yaml --service ingest --source weather_alerts
eml_transformer service-run --config configs/generated/aws-dev.runtime.yaml --service standardize --source weather_alerts
eml_transformer service-run --config configs/generated/aws-dev.runtime.yaml --service embed --source weather_alerts
eml_transformer service-run --config configs/generated/aws-dev.runtime.yaml --service backfill --source iem_afos --start-date 2026-01-01 --end-date 2026-01-07
eml_transformer gdelt-discover --config configs/generated/aws-dev.runtime.yaml --date today
eml_transformer gdelt-enqueue-urls --config configs/generated/aws-dev.runtime.yaml --key bronze/gdelt/candidate_urls/table=gkg/date=YYYY-MM-DD/timestamp=YYYYMMDDHHMMSS/parser_version=gdelt_gkg_v1/filter_version=weather_outage_us_v1/candidate_urls.jsonl
eml_transformer article-fetch-worker --config configs/generated/aws-dev.runtime.yaml --max-messages 50
eml_transformer aws-start-service --config configs/generated/aws-dev.runtime.yaml --service ingest --source weather_alerts
eml_transformer aws-start-service --config configs/generated/aws-dev.runtime.yaml --service gdelt_discovery --date today --state-machine
eml_transformer aws-restore-s3-object --config configs/generated/aws-dev.runtime.yaml --key bronze/gdelt/raw/table=gkg/date=YYYY-MM-DD/timestamp=YYYYMMDDHHMMSS/YYYYMMDDHHMMSS.gkg.csv.zip
eml_transformer aws-s3-restore-status --config configs/generated/aws-dev.runtime.yaml --key bronze/gdelt/raw/table=gkg/date=YYYY-MM-DD/timestamp=YYYYMMDDHHMMSS/YYYYMMDDHHMMSS.gkg.csv.zip
eml_transformer aws-rehydrate-s3-object --config configs/generated/aws-dev.runtime.yaml --key bronze/gdelt/raw/table=gkg/date=YYYY-MM-DD/timestamp=YYYYMMDDHHMMSS/YYYYMMDDHHMMSS.gkg.csv.zip
```

CDK/CloudFormation outputs should be rendered into a runtime YAML for local
testing or injected as environment variables in AWS Batch:

```text
DATA_BUCKET
STORAGE_PREFIX
URL_FETCH_QUEUE_URL
ARTICLE_URL_QUEUE_URL
URL_STATE_TABLE
RUN_STATE_TABLE
DOMAIN_THROTTLE_TABLE
STATE_MACHINE_ARN
SOURCE_WORKFLOW_ARN
BACKFILL_WORKFLOW_ARN
BATCH_JOB_QUEUE
GDELT_MAX_URLS_PER_RUN
BATCH_JOB_DEFINITION
BATCH_JOB_DEFINITION_INGEST
BATCH_JOB_DEFINITION_STANDARDIZE
BATCH_JOB_DEFINITION_EMBED
BATCH_JOB_DEFINITION_BACKFILL
BATCH_JOB_DEFINITION_RUN_ALL
BATCH_JOB_DEFINITION_GDELT_DISCOVERY
BATCH_JOB_DEFINITION_URL_FETCH_WORKER
BATCH_JOB_DEFINITION_S3_RESTORE_OPERATOR
AWS_REGION
INFRA_STACK
CDK_STACK
TERRAFORM_STACK
```

### Operational Defaults

- GDELT discovery is chunked by 15-minute GKG files.
- Raw GDELT files are cached through the configured `Storage` backend, so the same logic works for local files, S3, or another future storage implementation.
- Parsed GDELT candidate URL outputs are keyed by source timestamp plus parser/filter version, not by run ID, so unchanged files can be reused across runs.
- Generic registry sources currently write one bronze JSONL marker plus S3 append parts, one silver parquet, and one gold parquet per source/model. They are not partitioned by run date.
- URL deduplication is based on canonicalized URL hashes.
- DynamoDB conditional writes protect the SQS queue from duplicate article URL messages.
- Article fetch workers delete SQS messages only after successful S3 writes.
- Failed fetches remain retryable and move to the dead-letter queue according to the infrastructure SQS redrive policy.
- Run-state records use `run_id` and `job_type` so multi-step workflows do not overwrite their own state.
- CloudWatch metrics are best effort and should not fail ingestion.
- Dev schedules are disabled by default; prod may enable hourly GDELT acquisition through the deployment YAML.
