# Run Location, Test, And Deployment Report

Generated from the current repository state on 2026-06-16.

## Core Concepts

There are two config shapes in the repo:

| Config type | Files | Purpose | Used by |
|---|---|---|---|
| Base runtime profile | `configs/local.yaml`, `configs/hpc.yaml`, `configs/aws.yaml` | Defines storage, sources, services, embeddings, and runtime defaults. | Local CLI and HPC scripts can run these directly. |
| Deployment layer | `configs/deployments/*.yaml` | Selects a base profile, overrides stack identity, service limits, schedules, cost controls, and runtime output paths. | `config-validate`, `deployment-info`, `deployment-matrix`, `config-render`, CDK, and AWS test scripts. |
| Generated runtime config | `configs/generated/<deployment>.runtime.yaml` | Concrete runtime values for a selected deployment. For AWS this should come from CloudFormation outputs after deploy. | Runtime CLI commands, Batch containers, local AWS testing. |

Runtime commands such as `ingest`, `standardize`, `embed`, `backfill`, and
`service-run` read a runtime config. Deployment YAML files should be validated
and rendered first when you want the final layered state.

## Run Location Summary

| Run location | Current config entry point | Execution model | Storage | Deployment method | Best use |
|---|---|---|---|---|---|
| Local Python | `configs/local.yaml` or rendered `configs/generated/local.runtime.yaml` | Direct `eml_transformer` CLI in a Python environment. | Local filesystem under `data/`. | No infrastructure deploy. | Fast development, source ingestion tests, backfills, dry-run GDELT discovery. |
| Local Docker | `DEPLOYMENT_CONFIG=configs/deployments/<name>.yaml` for AWS validation, or mounted runtime config for CLI. | Docker image from `Dockerfile`; entrypoint is `eml_transformer`. Direct AWS validation commands build container commands and write logs to `artifacts/aws_test_results`. | Local filesystem or mounted generated config; AWS commands can mount `~/.aws`. | Docker build only, unless running AWS preflight. | Reproducible tests, container parity with AWS Batch. |
| HPC / Slurm | `configs/hpc.yaml`, or rendered `configs/generated/hpc-dev.runtime.yaml`. | Direct Python launched by Slurm scripts under `scripts/run.sh`, `scripts/run_ingestion.sh`, and `scripts/test_hpc.sh`. | Local/shared filesystem under `data/`. | Slurm submission with `sbatch`; no CDK or cloud infrastructure deploy. | GPU embeddings, larger local filesystem jobs, scheduled ingestion/backfill loops. |
| AWS dev/prod/test | `configs/deployments/aws-dev.yaml`, `aws-prod.yaml`, or optional `aws-smoke.yaml`. | CDK deploys AWS resources; AWS Batch/Fargate runs the same container image with service commands. Step Functions and EventBridge Scheduler orchestrate workflows. | S3 data lake plus DynamoDB run/URL state and SQS queues. | CDK, AWS preflight, `run_all.sh`, or GitHub Actions workflow. | Full cloud workflow, queue-based GDELT/article fetch, backfill workflows, diagnostics. |

## Current Deployment Targets

| Deployment | Engine | Stack | Runtime config path | Schedules | Main limits and notes |
|---|---|---|---|---|---|
| `local` | `local` | `eml-transformer-local` | `configs/generated/local.runtime.yaml` | Disabled | Local storage, CPU embeddings by default, URL fetch worker enabled in config but requires queue values to do real queue work. |
| `hpc-dev` | `hpc` | `eml-transformer-hpc-dev` | `configs/generated/hpc-dev.runtime.yaml` | Disabled | GPU embeddings enabled with `device: cuda`; GDELT discovery and URL fetch worker disabled in the HPC base profile. |
| `aws-dev` | `cdk` | `eml-transformer-dev` | `configs/generated/aws-dev.runtime.yaml` | Disabled | Dev Batch/Fargate stack, no NAT Gateway by default, URL fetch limit `max_messages: 50`, output batch size `100`. |
| `aws-prod` | `cdk` | `eml-transformer-prod` | `configs/generated/aws-prod.runtime.yaml` | GDELT hourly | Production shape, URL fetch limit `max_messages: 200`, output batch size `250`; still requires real account/network/alert values before production use. |
| `aws-smoke` | `cdk` | `eml-transformer-smoke` | `configs/generated/aws-smoke.runtime.yaml` | Accelerated 10 minute test schedules | Optional low-cost test target: `max_batch_vcpus: 2`, GDELT `max_files: 1`, `max_urls_per_run: 5`, URL fetch `max_messages: 5`, SNS enabled, NewsAPI secret injection configured. |

`aws-smoke.yaml` is now just one optional deployment target. The scripts do not
default to it; they require `DEPLOYMENT_CONFIG`.

## Test Inventory

### Unit And Contract Tests

| Test file | What it covers | Recommended run mode |
|---|---|---|
| `tests/unit/` | Fast unit coverage for deployment config logic, direct AWS validation command construction, cleanup planning, GDELT/AWS runtime helpers, original compatibility, and backfill behavior. | Local `pytest`; no Docker, AWS credentials, Node, or live network required. |
| `tests/contract/test_deployment_config.py` | Validates every real deployment file, renders runtime/matrix outputs, and writes generated JSON evidence. | Local or container pytest with test dependencies. |
| `tests/aws_contract/test_cdk_stack.py` | CDK synthesis for every CDK deployment, resource counts, SNS/runtime secret wiring, Step Functions definitions, Batch job environment contract. | CDK test container or local Python with Node and CDK deps. |
| `tests/unit/test_backfill_workflow.py` | Backfill date windows, checkpoint seeding, Batch and Step Functions backfill submission payloads. | Local or container pytest. |

### AWS Validation Commands

| Command | Runs where | AWS required | What happens | Logs |
|---|---|---|---|---|
| `eml_transformer aws-preflight` | Host/WSL plus Docker | Yes | SSO check, optional secret ARN resolution, VPC defaults, Docker build, CDK bootstrap/deploy, render runtime config, push image to ECR. | `artifacts/aws_test_results/preflight/` |
| `eml_transformer aws-validate-static` | Local Python | No | Runs unit tests, deployment contract tests, and `config-validate-all`. | `artifacts/aws_test_results/static/` and JSON under `deployment_config/` and `cdk_stack/` |
| `eml_transformer aws-validate-container` | Containers | No | Verifies CLI help/source registry and loops through every deployment for `config-validate`, `deployment-info`, and `deployment-matrix`. | `artifacts/aws_test_results/container/` |
| `eml_transformer aws-validate-infra` | Container for app checks | Yes | Verifies deployed S3, SQS, DynamoDB, Batch queue, SNS where configured. | `artifacts/aws_test_results/infra/` |
| `eml_transformer aws-validate-gdelt` | Containers | Yes | Runs GDELT dry-run and enqueue path with bounded `--max-files`, then queue checks and article fetch worker. | `artifacts/aws_test_results/gdelt/` |
| `eml_transformer aws-validate-pipeline` | Containers | Yes | Standardizes generic sources and runs embedding best-effort. | `artifacts/aws_test_results/pipeline/` |
| `eml_transformer aws-validate-batch` | Containers | Yes | Submits selected Batch jobs and polls them with `batch-wait`. | `artifacts/aws_test_results/batch/` |
| `eml_transformer aws-validate-e2e` | Containers/jobs | Yes | Starts the bounded backfill workflow and collects infrastructure diagnostics. | `artifacts/aws_test_results/e2e/` |

Full AWS test entry point:

```bash
DEPLOYMENT_CONFIG=configs/deployments/aws-dev.yaml \
AWS_PROFILE=episb \
bash scripts/aws/run_all.sh
```

Useful validation entry points:

```bash
eml_transformer aws-validate-static --deployment configs/deployments/aws-dev.yaml
eml_transformer aws-validate-container --deployment configs/deployments/aws-dev.yaml
```

## How Code Executes

| Execution path | Code path | Containerized | Notes |
|---|---|---|---|
| Local CLI | `eml_transformer <command> --config configs/local.yaml` | Optional | Uses local Python environment and local `data/` storage. |
| Local Docker CLI | `docker run eml-transformer:<tag> <command>` | Yes | `Dockerfile` installs package extras selected by `OPTIONAL_EXTRAS`; default entrypoint is `eml_transformer`. |
| AWS validation commands | `eml_transformer aws-validate-static`, `aws-validate-container`, and live AWS validators | Some | Direct command functions write durable results to `artifacts/aws_test_results/`. |
| CDK test image | `eml-transformer-cdk-test:<deployment>` | Yes | Built with `OPTIONAL_EXTRAS=aws,test,cdk` and `INSTALL_CDK_TOOLING=1`, so CDK synthesis does not silently skip. |
| AWS deploy | `cdk deploy -c deployment_config=<deployment>` or `aws-preflight` | Partly | CDK and ECR push run on the host or CI runner; Batch jobs run containers in AWS. |
| AWS Batch jobs | `service-run`, `gdelt-discover`, `article-fetch-worker` commands | Yes | CDK creates job definitions and injects runtime env vars. The container can run from env vars even when generated runtime YAML is absent. |
| HPC | `sbatch scripts/run.sh`, `run_ingestion.sh`, `test_hpc.sh` | No | Slurm launches direct Python. Scripts currently hard-code project path, account, partition, mail user, and module names. |
| GitHub Actions CDK workflow | `.github/workflows/cdk.yml` | No | Installs Python/CDK on the Ubuntu runner; it is not currently container-run. |

## What You Can Run By Location

| Capability | Local | HPC | AWS |
|---|---|---|---|
| Config validation and matrix | Yes: `config-validate`, `deployment-info`, `deployment-matrix`. | Yes, same CLI. | Yes locally before deploy and in CI. |
| Generic source ingestion | Yes: `ingest --source <source>`. | Yes through direct CLI or Slurm. | Yes through Batch `service-run ingest` or Step Functions source workflow. |
| Standardization | Yes. | Yes. | Yes through Batch/Step Functions. |
| Embeddings | Yes with CPU by default; heavy models may be slow. | Yes, preferred location because `configs/hpc.yaml` enables CUDA. | Configured but disabled in current AWS deployments by default. |
| Backfill | Yes for sources with `supports_backfill=True`. | Yes for supported sources, suitable for larger history windows. | Yes through direct Batch or backfill Step Functions workflow. |
| GDELT dry-run discovery | Yes with `--no-enqueue`; writes raw/candidate artifacts to local storage. | Possible through direct CLI if GDELT config is enabled or using an exploration script; disabled in current HPC base profile. | Yes. |
| GDELT queueing and article fetch | Only if AWS queue/table runtime values are supplied. | Not currently configured. | Yes through SQS, DynamoDB, Batch, and Step Functions. |
| S3 restore/rehydration | No, unless pointed at AWS runtime config. | No, unless pointed at AWS runtime config. | Yes: `aws-restore-s3-object`, `aws-s3-restore-status`, `aws-rehydrate-s3-object`. |
| Infrastructure deploy | None. | None. | CDK deploy creates S3, SQS, DynamoDB, Batch, Step Functions, Scheduler, IAM, CloudWatch, ECR, optional SNS/Budget. |
| Orchestration | Manual CLI. | Slurm scripts and optional self-resubmission. | Step Functions, EventBridge Scheduler, Batch queues, and direct AWS validation commands. |

## Backfills And Historical Downloads

### Generic Backfill

Backfill uses date windows and stops early if any window fails.

```bash
eml_transformer backfill \
  --config configs/local.yaml \
  --source iem_afos \
  --start-date 2026-01-01 \
  --end-date 2026-01-31 \
  --window-days 7 \
  --init-checkpoint
```

| Source | Update mode | Supports `backfill` | Notes |
|---|---|---|---|
| `newsapi` | `incremental` | Yes | Requires `NEWSAPI_KEY`; subject to NewsAPI plan/rate/history limits. Uses `from` and `to` request parameters. |
| `iem_afos` | `incremental` | Yes | Uses `sdate` and `edate`; configurable WFOs, product types, PIL, limit, format, timeout. |
| `weather_alerts` | `snapshot` | No | Current/live snapshot source, not historical backfill. |
| `miso_notifications` | `snapshot` | No | Current snapshot source, not historical backfill. |
| `gdelt` | Acquisition service, not generic source backfill | Use `gdelt-discover` by date | GDELT is handled by the acquisition path, not `BackfillPipeline`. |

Backfill controls:

| Control | Where set | Effect |
|---|---|---|
| `--start-date`, `--end-date` | CLI or AWS submission parameters | Defines inclusive historical range. |
| `--window-days` | CLI, AWS submission parameters, or `services.backfill.window_days` default | Splits a long range into bounded API calls. Must be at least 1. |
| `--init-checkpoint` | CLI, AWS submission parameters, or `services.backfill.init_checkpoint` default | Seeds the incremental checkpoint to the day after the final backfilled window. |
| `--source all` | CLI | Runs only sources that explicitly support backfill. |

AWS backfill submission examples:

```bash
eml_transformer aws-start-service \
  --config configs/generated/aws-dev.runtime.yaml \
  --service backfill \
  --source iem_afos \
  --start-date 2026-01-01 \
  --end-date 2026-01-31 \
  --window-days 7

eml_transformer aws-start-service \
  --config configs/generated/aws-dev.runtime.yaml \
  --service backfill \
  --source all \
  --start-date 2026-01-01 \
  --end-date 2026-01-31 \
  --window-days 7 \
  --state-machine
```

### GDELT Historical Exploration

GDELT is available as a per-day acquisition command. One UTC day has 96
15-minute GKG files.

```bash
eml_transformer gdelt-discover \
  --config configs/local.yaml \
  --date 2026-01-01 \
  --max-files 4 \
  --max-urls 25 \
  --no-enqueue
```

Use `--max-files` for sample/statistical exploration and remove it for a full
day. Use `--max-urls` to cap candidate URLs. Use `--no-enqueue` locally when no
SQS queue is available.

Current exploration scripts:

| Script | Purpose | Current limitation |
|---|---|---|
| `scripts/explore_gdelt.py` | Standalone one-day GDELT GKG download, filter, summary, domain counts, sampled URLs, CSV output. | `DATE` is hard-coded in the script; not integrated with deployment config or CLI arguments. |
| `scripts/explore_iem_afos.py` | Quick IEM AFOS retrieval and parsing experiment. | Hard-coded params; exploration script, not pipeline workflow. |

For repeatable historical GDELT exploration, prefer a small wrapper loop around
`gdelt-discover --no-enqueue` so artifacts land in the normal storage layout.

## Configuration Knobs

| Section | Applies to | Controls |
|---|---|---|
| `deployment.*` | Deployment files | Deployment name and base profile selection. |
| `runtime.config_path` | Deployment files | Where rendered runtime YAML should be written. |
| `infra.*` | All engines | Engine type, stack name, region, environment, project identity. |
| `cost.*` | Mostly AWS | Budget target, log retention, NAT Gateway allowance, Batch vCPU cap, retained ECR image count. |
| `network.*` | AWS | Batch subnet IDs, security group IDs, public IP assignment. |
| `storage.*` | All engines | Local base directory or S3 backend/bucket/prefix/lifecycle/restore behavior. |
| `services.<service>.enabled` | All engines | Whether a service is part of the deployment/runtime profile. |
| `services.<service>.compute.*` | AWS and scheduler planning | vCPU, memory, and timeout values for jobs. |
| `services.<service>.schedule.*` | AWS | EventBridge Scheduler enablement and rate/cron expression. |
| `services.url_fetch_worker.limits.*` | AWS URL fetch | Max messages, request delay, output batch size, output format. |
| `sources.<source>.*` | All engines | Source-specific enablement, API parameters, limits, and GDELT acquisition caps. |
| `embeddings.*` | Local/HPC/AWS runtime | Embedding provider, model, device, batch size, normalization, text columns. |
| `hpc.*` | HPC | Scheduler metadata such as Slurm partition/account/default time and GPU preference. |
| `runtime_secrets.*` | AWS | Secrets Manager ARN env-var contract for Batch containers. |
| `notifications.sns.*` | AWS | SNS topic, email recipients, workflow notifications, alarm actions. |

## Deployment Paths

| Path | Command pattern | What deploys |
|---|---|---|
| Local runtime render | `eml_transformer config-render --deployment configs/deployments/local.yaml --output configs/generated/local.runtime.yaml` | No infrastructure; creates a runtime file. |
| HPC runtime render | `eml_transformer config-render --deployment configs/deployments/hpc-dev.yaml --output configs/generated/hpc-dev.runtime.yaml` | No infrastructure; creates a runtime file for Slurm/direct CLI. |
| Slurm job | `sbatch scripts/run_ingestion.sh` or `sbatch scripts/run.sh` | Schedules a job on the HPC cluster. Current scripts call `configs/hpc.yaml`. |
| AWS config preview | `eml_transformer deployment-matrix --deployment configs/deployments/aws-dev.yaml` | No infrastructure; prints intended runtime and service matrix. |
| AWS CDK synth | `cd infra/cdk && cdk synth -c deployment_config=configs/deployments/aws-dev.yaml` | No deploy; generates CloudFormation template. |
| AWS CDK deploy | `cd infra/cdk && cdk deploy -c deployment_config=configs/deployments/aws-dev.yaml` | Deploys AWS infrastructure. |
| AWS preflight | `DEPLOYMENT_CONFIG=configs/deployments/aws-dev.yaml bash scripts/aws/deploy/preflight.sh` | Deploys stack, builds/pushes image, renders runtime config from outputs. |
| Full AWS validation suite | `DEPLOYMENT_CONFIG=configs/deployments/aws-dev.yaml bash scripts/aws/run_all.sh` | Runs preflight, static/container checks, and live AWS validations. |
| GitHub Actions | `.github/workflows/cdk.yml` workflow dispatch | Installs dependencies on runner, tests, synthesizes all CDK deployments, optionally deploys selected deployment. |

## Recommended Workflows

### Local Development

```bash
python -m pip install -e ".[aws,test]"
python -m pytest tests/unit -q
eml_transformer config-validate --deployment configs/deployments/local.yaml
eml_transformer config-render --deployment configs/deployments/local.yaml --output configs/generated/local.runtime.yaml
eml_transformer ingest --config configs/local.yaml --source all
eml_transformer standardize --config configs/local.yaml --source all
eml_transformer backfill --config configs/local.yaml --source iem_afos --start-date 2026-01-01 --end-date 2026-01-07 --window-days 3
eml_transformer gdelt-discover --config configs/local.yaml --date 2026-01-01 --max-files 2 --no-enqueue
```

### Container Validation

```bash
docker build --build-arg OPTIONAL_EXTRAS=aws,test -t eml-transformer:aws-dev .
eml_transformer aws-validate-container --deployment configs/deployments/aws-dev.yaml
```

### HPC

```bash
python -m pip install -e ".[hpc]"
eml_transformer config-render --deployment configs/deployments/hpc-dev.yaml --output configs/generated/hpc-dev.runtime.yaml
sbatch scripts/test_hpc.sh
sbatch scripts/run_ingestion.sh
sbatch scripts/run.sh
```

For a parameterized historical job, use the CLI command inside a Slurm script:

```bash
python -m eml_transformer.cli backfill \
  --config configs/hpc.yaml \
  --source iem_afos \
  --start-date 2026-01-01 \
  --end-date 2026-02-01 \
  --window-days 7
```

### AWS

```bash
export AWS_PROFILE=episb
export DEPLOYMENT_CONFIG=configs/deployments/aws-dev.yaml

eml_transformer deployment-info --deployment "$DEPLOYMENT_CONFIG"
eml_transformer config-validate --deployment "$DEPLOYMENT_CONFIG"
eml_transformer deployment-matrix --deployment "$DEPLOYMENT_CONFIG"

bash scripts/aws/deploy/preflight.sh
eml_transformer aws-validate-static --deployment "$DEPLOYMENT_CONFIG"
eml_transformer aws-validate-container --deployment "$DEPLOYMENT_CONFIG"
bash scripts/aws/run_all.sh
```

## Current Gaps And Cautions

| Area | Current state |
|---|---|
| HPC deployment automation | Slurm scripts exist, but they are hard-coded for one project path/account/module/mail user and call `configs/hpc.yaml`. They are not yet generated from `configs/deployments/hpc-dev.yaml`. |
| GDELT date-range history | There is no first-class `gdelt-backfill --start-date --end-date` command. Use a loop over `gdelt-discover --date ... --no-enqueue` for exploration. |
| Local queue/article fetch | `article-fetch-worker` and URL enqueue require SQS/DynamoDB runtime values. Local dry-run discovery works; local queue processing is not implemented. |
| AWS production readiness | `aws-prod.yaml` still has placeholder network/account/alert values and should be completed before production deploy. |
| GitHub CDK workflow | It is runner-installed, not container-run. Local CDK contract tests can run in a CDK-enabled container. |
| Generic historical storage | Generic source outputs are source-level JSONL/parquet paths, not partitioned by run date. Backfill dedupe/checkpoints reduce duplicate records, but statistical history should preserve run manifests separately when needed. |
