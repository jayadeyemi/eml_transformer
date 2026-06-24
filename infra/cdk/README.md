# AWS CDK Collection Infrastructure

AWS CDK in Python is the primary infrastructure path for AWS deployments. CDK
reads the layered deployment YAML, creates durable AWS resources, and injects
runtime values into Batch job definitions. Runtime Python code must not create
durable infrastructure.
Real deployments use generated runtime YAML files under `configs/generated/`;
those files stay out of git and are ignored by `.gitignore`.

AWS deployment configs compose `configs/aws.yaml`, which expands source layers,
then the selected file under `configs/deployments/`.

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

CDK tags every deployment resource with `infra_stack=<stack_name>`. AWS Budgets
created by the stack are filtered to `user:infra_stack=<stack_name>`, so each
deployment budget tracks only resources carrying that deployment tag. Batch job
definitions propagate those tags to the Fargate tasks they launch so runtime
compute is attributable to the same deployment.

AWS Billing must have the user-defined cost allocation tag `infra_stack`
activated before tag-filtered budgets and anomaly monitors can report those
tagged costs. Until activation finishes propagating in AWS Billing, the budget
resource exists but its spend attribution can lag or appear incomplete.

For the shortest low-cost AWS test, select a low-capacity deployment config,
submit one capped `gdelt_discovery` job with `--max-files 1`, then destroy the
selected stack if you do not need the retained data resources. The full test
uses `DEPLOYMENT_CONFIG=<config> scripts/aws/run_all.sh --reset-stack`;
runtime secrets and SNS notifications are enabled only when the selected
deployment declares them.
