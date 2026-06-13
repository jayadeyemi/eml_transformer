# AWS CDK Collection Infrastructure

AWS CDK in Python is the primary infrastructure path for AWS deployments. CDK
reads the layered deployment YAML, creates durable AWS resources, and injects
runtime values into Batch job definitions. Runtime Python code must not create
durable infrastructure.

Terraform remains under `infra/terraform/aws/` as a secondary compatibility and
reference implementation. Do not deploy CDK and Terraform into the same
environment unless resources have first been imported/migrated deliberately.

`configs/aws.example.yaml` uses fake account/resource values and is safe as a
public example only. Real deployments should use generated runtime YAML files
under `configs/generated/`; those files stay out of git and are ignored by
`.gitignore`.

## Validate And Render Config

```bash
eml_transformer config-validate --deployment configs/deployments/aws-dev.yaml
eml_transformer deployment-matrix --deployment configs/deployments/aws-dev.yaml
eml_transformer config-render \
  --deployment configs/deployments/aws-dev.yaml \
  --output configs/generated/aws-dev.runtime.yaml
```

Render the runtime config before building an image for AWS Batch. The Dockerfile
copies `configs/`, so ignored generated runtime configs are included only when
they exist in the build context.

## Synthesize And Deploy

```bash
cd infra/cdk
python -m pip install -r requirements.txt
cdk synth -c deployment_config=configs/deployments/aws-dev.yaml
cdk diff -c deployment_config=configs/deployments/aws-dev.yaml
cdk deploy -c deployment_config=configs/deployments/aws-dev.yaml
```

Run the commands from `infra/cdk`; relative deployment paths are resolved from
the repository root.

CDK requires Node.js and the CDK CLI. Install the CLI with:

```bash
npm install -g aws-cdk
```

## Build And Push Image

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

docker build -t eml-transformer-ingestion .
docker tag eml-transformer-ingestion:latest <ecr-repository-url>:latest
docker push <ecr-repository-url>:latest
```

## Cost Controls

Dev schedules are disabled by default. The dev deployment keeps log retention at
14 days, avoids NAT Gateway creation, caps Batch Fargate capacity through
`cost.max_batch_vcpus`, and keeps AWS GPU embedding disabled. The deployment
matrix reports service topology, runtime environment keys, and job-definition
mappings; it does not calculate a monthly cost estimate.

For the shortest low-cost AWS test, deploy `configs/deployments/aws-smoke.yaml`,
submit one capped `gdelt_discovery` job with `--max-files 1`, then destroy the
smoke stack if you do not need the retained data resources.
