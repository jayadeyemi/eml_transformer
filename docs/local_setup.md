# Local Setup and AWS Deployment Guide

## Prerequisites

All commands below are run in **WSL2 (Ubuntu on Windows)** unless noted.
The ingestion containers run in Docker on AWS Fargate; the commands here are
for setting up and operating the infrastructure from your local machine.

### 1 - Windows tools

| Tool | Install |
|------|---------|
| WSL2 + Ubuntu | `wsl --install` in Windows PowerShell (Admin) |
| Docker Desktop | <https://docs.docker.com/desktop/install/windows-install/> - enable WSL2 backend |
| Windows Terminal | <https://aka.ms/terminal> (recommended) |

### 2 - WSL2 tools

```bash
# System packages
sudo apt update && sudo apt install -y python3-pip python3-venv git unzip curl

# AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install
aws --version   # should print aws-cli/2.x.x

# Node.js (LTS) and AWS CDK CLI
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
npm install -g aws-cdk
cdk --version   # should print 2.x.x

# Python project setup (run from the repo root)
python3 -m pip install -e .
python3 -m pip install -r infra/cdk/requirements.txt
```

### 3 - Verify Docker from WSL2

```bash
docker run --rm hello-world   # must succeed before building images
```

---

## AWS SSO Configuration

### Landing page

Your AWS access portal is at:

```
https://<your-aws-access-portal>.awsapps.com/start
```

Open that URL in a browser to see all available AWS accounts and roles.

### One-time profile setup (modern SSO session format)

The modern format separates the SSO session from the profile so that all
profiles can share a single login token.

```bash
# Step 1 - create the SSO session
aws configure sso-session
#   SSO session name: episb
#   SSO start URL:    https://<your-aws-access-portal>.awsapps.com/start
#   SSO region:       us-east-2
#   Registration scopes: sso:account:access   (press Enter for default)
```

This writes the following block to `~/.aws/config`:

```ini
[sso-session episb]
sso_start_url = https://<your-aws-access-portal>.awsapps.com/start
sso_region = us-east-2
sso_registration_scopes = sso:account:access
```

```bash
# Step 2 - create a named profile that links to the session
# Choose a profile name you will remember (e.g. eml-dev).
aws configure sso --profile eml-dev
#   SSO session name:  episb
#   Account ID:        <aws-account-id>
#   Role:              <sso-role-name>
#   Default region:    us-east-1
#   Output format:     yaml
```

This appends to `~/.aws/config`:

```ini
[profile eml-dev]
sso_session     = episb
sso_account_id  = <aws-account-id>
sso_role_name   = <sso-role-name>
region          = us-east-1
output          = yaml
```

### Verify the profile

```bash
aws sts get-caller-identity --profile eml-dev
# Expected:
# Account: '<aws-account-id>'
# Arn: arn:aws:sts::<aws-account-id>:assumed-role/AWSReservedSSO_<sso-role-name>_.../...
```

---

## Logging In (Session Start)

### Browser opens automatically

```bash
aws sso login --profile eml-dev
# A browser tab opens -> click "Allow" -> terminal shows:
# Successfully logged into Start URL: https://<your-aws-access-portal>.awsapps.com/start
```

### No browser (WSL2 / Docker / headless)

WSL2 may fail to open a browser (`gio: Operation not supported`). Use the
device-code flow instead, which lets you paste the URL into any browser:

```bash
aws sso login --profile eml-dev --use-device-code
# Prints a URL -> open it in any Windows browser -> click "Allow"
```

### Session expiry and reconnection

SSO tokens last **8 hours** by default. When a command fails with
`Token has expired` or `An error occurred (AuthExpiredException)`, just
re-login:

```bash
aws sso login --profile eml-dev --use-device-code
```

No other config needs to change; the credential cache is refreshed
automatically under `~/.aws/sso/cache/`.

---

## Post-login: Set the active profile

Set `AWS_PROFILE` so every command uses `eml-dev` without repeating
`--profile` each time:

```bash
export AWS_PROFILE=eml-dev
# Optional: add to ~/.bashrc or ~/.zshrc so it persists across terminals
echo 'export AWS_PROFILE=eml-dev' >> ~/.bashrc
```

Or pass `--profile eml-dev` explicitly to every CLI command (see below).

---

## Running CDK and CLI Commands

### Render a runtime config preview

```bash
AWS_ACCOUNT_ID=<aws-account-id> eml_transformer config-render \
    --deployment configs/deployments/aws-dev.yaml \
    --output configs/generated/aws-dev.runtime.yaml
```

### Validate configs

```bash
eml_transformer config-validate-all --directory configs/deployments
```

### CDK synth (generate CloudFormation template)

```bash
cd infra/cdk
cdk synth \
    -c deployment_config=configs/deployments/aws-dev.yaml \
    -c aws_profile=eml-dev
```

### CDK diff (preview changes vs deployed stack)

```bash
cd infra/cdk
cdk diff \
    -c deployment_config=configs/deployments/aws-dev.yaml \
    -c aws_profile=eml-dev
```

### CDK deploy

```bash
cd infra/cdk
cdk deploy \
    -c deployment_config=configs/deployments/aws-dev.yaml \
    -c aws_profile=eml-dev
```

### Regenerate runtime config from deployed stack outputs

After a deploy, produce a runtime config from actual CloudFormation outputs
(authoritative ARNs, not deterministic predictions):

```bash
eml_transformer config-render-from-outputs \
    --stack eml-transformer-dev \
    --region us-east-1 \
    --profile eml-dev \
    --output configs/generated/aws-dev.runtime.yaml
```

`config-render-from-outputs` renders live AWS resource values. It does not
recreate the full source configuration by itself. For local smoke commands, the
generated runtime file must also contain the normal `sources`, `paths`, and
pipeline settings from the deployment config layers. Re-render or merge those
settings before running `ingest`, `standardize`, `embed`, or `run-all` locally.

See `docs/aws_s3_layout.md` for the S3 folders that each command reads and
writes.

---

## Changing Infrastructure After Deployment

All infrastructure parameters live in the YAML configs under
`configs/deployments/` and `configs/base.yaml`. Nothing is hardcoded in the
CDK stack. To update any infrastructure setting:

1. **Edit the YAML.** Examples:

   ```yaml
   # configs/deployments/aws-dev.yaml
   cost:
     max_batch_vcpus: 8    # was 32

   services:
     gdelt_discovery:
       schedule:
         enabled: true
         expression: rate(30 minutes)

     url_fetch_worker:
       limits:
         max_messages: 100
         worker_parallelism: 2   # add parallel fetch workers in state machine

   storage:
     lifecycle:
       bronze_glacier_ir_days: 60   # was 90
   ```

2. **Validate the change:**

   ```bash
   eml_transformer config-validate --deployment configs/deployments/aws-dev.yaml
   ```

3. **Preview the CloudFormation diff:**

   ```bash
   cd infra/cdk
   cdk diff -c deployment_config=configs/deployments/aws-dev.yaml -c aws_profile=eml-dev
   ```

4. **Deploy:**

   ```bash
   cdk deploy -c deployment_config=configs/deployments/aws-dev.yaml -c aws_profile=eml-dev
   ```

5. **Refresh the runtime config:**

   ```bash
   eml_transformer config-render-from-outputs \
       --stack eml-transformer-dev --profile eml-dev \
       --output configs/generated/aws-dev.runtime.yaml
   ```

### What each config section controls

| Section | Controls |
|---------|----------|
| `infra.*` | Stack name, region, environment name |
| `cost.*` | Batch vCPU cap, log retention, ECR image count, budget alert emails |
| `network.*` | VPC subnet IDs and security group IDs |
| `storage.lifecycle.*` | S3 Glacier/Deep Archive transition days |
| `services.<name>.compute.*` | Fargate vCPU and memory per job definition |
| `services.<name>.limits.*` | Message caps, batch sizes, worker parallelism |
| `services.<name>.schedule.*` | Enable/disable EventBridge schedule and cron expression |
| `sources.*` | Per-source acquisition limits (max files, URLs) |

Backfill uses `services.backfill.window_days` and
`services.backfill.init_checkpoint` as job-definition defaults. Runtime
submissions can override `source`, `start_date`, `end_date`, `window_days`, and
`init_checkpoint`.

---

## Docker Build and Push (local test)

Before pushing you must be logged in to ECR:

```bash
ACCOUNT_ID=<aws-account-id>
REGION=us-east-1
STACK=eml-transformer-dev

aws ecr get-login-password --region $REGION --profile eml-dev \
    | docker login --username AWS --password-stdin \
      "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# Build
docker build -t eml-transformer-ingestion .

# Tag and push (use a meaningful tag, not just 'latest')
IMAGE_TAG=$(git rev-parse --short HEAD)
REPO_URL="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$STACK-collection"
docker tag eml-transformer-ingestion:latest "$REPO_URL:$IMAGE_TAG"
docker push "$REPO_URL:$IMAGE_TAG"

# Re-deploy with the new image tag
cd infra/cdk
cdk deploy \
    -c deployment_config=configs/deployments/aws-dev.yaml \
    -c aws_profile=eml-dev \
    -c image_tag="$IMAGE_TAG"
```

---

## Running the Ingestion CLI Locally (outside Docker)

For local development and smoke tests against real AWS resources:

```bash
# Export the profile so all boto3 calls use it
export AWS_PROFILE=eml-dev

# Discover GDELT files for today (dry run, no enqueue)
eml_transformer gdelt-discover \
    --config configs/generated/aws-dev.runtime.yaml \
    --date today \
    --max-files 1 \
    --no-enqueue

# Start a Batch job via the CLI
eml_transformer aws-start-service \
    --config configs/generated/aws-dev.runtime.yaml \
    --service gdelt_discovery \
    --date today \
    --max-files 1 \
    --profile eml-dev

# Start a parameterized backfill through Step Functions
eml_transformer aws-start-service \
    --config configs/generated/aws-dev.runtime.yaml \
    --service backfill \
    --source all \
    --start-date 2026-01-01 \
    --end-date 2026-01-31 \
    --window-days 7 \
    --state-machine \
    --profile eml-dev
```

---

## Smoke Test Sequence

Run this against the `aws-smoke` stack before any real data collection. The
smoke stack uses low Batch capacity, schedules disabled, and the `smoke` image
tag.

```bash
export AWS_PROFILE=eml-dev
export AWS_ACCOUNT_ID=<aws-account-id>

# 1 - validate
eml_transformer config-validate --deployment configs/deployments/aws-smoke.yaml

# 2 - bootstrap once per account/region if not already bootstrapped
cd infra/cdk
cdk bootstrap aws://$AWS_ACCOUNT_ID/us-east-1 --profile eml-dev

# 3 - synth
cdk synth -c deployment_config=configs/deployments/aws-smoke.yaml -c aws_profile=eml-dev

# 4 - deploy infrastructure
cdk deploy -c deployment_config=configs/deployments/aws-smoke.yaml -c aws_profile=eml-dev
cd ../..

# 5 - get runtime config from CDK outputs
eml_transformer config-render-from-outputs \
    --stack eml-transformer-smoke \
    --profile eml-dev \
    --output configs/generated/aws-smoke.runtime.yaml

# 6 - build and push the image used by Batch
ACCOUNT_ID=$AWS_ACCOUNT_ID
REGION=us-east-1
REPO_URL="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/eml-transformer-smoke-collection"

aws ecr get-login-password --region "$REGION" --profile eml-dev \
    | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

docker build -t eml-transformer:smoke .
docker tag eml-transformer:smoke "$REPO_URL:smoke"
docker push "$REPO_URL:smoke"

# 7 - run the full smoke suite
bash scripts/aws_test/run_all.sh --skip-phase0

# 8 - tear down when done (data bucket and tables are RETAINED by design)
cd infra/cdk
cdk destroy -c deployment_config=configs/deployments/aws-smoke.yaml -c aws_profile=eml-dev
```

The smoke suite writes logs under `scripts/aws_test/results/`. If `NEWSAPI_KEY`
is not present, the NewsAPI source is skipped. The embedding smoke is currently
best-effort unless the image includes the optional HPC/modeling dependencies.

---

## Troubleshooting

### `Token has expired` / `AuthExpiredException`

```bash
aws sso login --profile eml-dev --use-device-code
```

### `An error occurred (Configuration): Missing the following required SSO configuration values`

The profile is using the legacy format without an SSO session. Re-create it
with `aws configure sso --profile eml-dev` and enter the session name `episb`.

### `gio: ... Operation not supported` (browser fails to open in WSL2)

Add `--use-device-code` to the `aws sso login` command and open the printed
URL in a Windows browser.

### `No module named 'eml_transformer'`

```bash
cd <repo-root>
python3 -m pip install -e .
```

### CDK `node` or `cdk` not found

```bash
sudo apt install -y nodejs
npm install -g aws-cdk
```

### Docker `permission denied` in WSL2

Add your user to the Docker group (requires re-login):

```bash
sudo usermod -aG docker $USER
```

Or use `sudo docker ...` for a one-off fix.
