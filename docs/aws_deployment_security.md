# AWS Deployment Security And Cost Notes

`configs/aws.example.yaml` intentionally uses the fake account ID
`123456789012` and fake ARNs. It is safe as a public example. Real generated
runtime configs, CloudFormation outputs, and account/resource metadata should
not be committed to a public repository. Do not use `configs/aws.example.yaml`
as an operational deployment config.

## Runtime Values

Runtime containers should receive real resource values from one of these paths:

- AWS CDK/CloudFormation outputs
- Batch job-definition environment variables
- CI/CD secrets or deployment environment variables
- Locally generated runtime YAML kept out of git

Generated runtime YAML files under `configs/generated/*.yaml` are ignored by
git. Use `AWS_ACCOUNT_ID=<account-id>` only when intentionally rendering a real
local runtime config.

## Access Control

Access is controlled through IAM roles and policies:

- Step Functions can be started only by principals allowed through IAM; no
  public resource policy is created by the CDK stack.
- AWS Batch queues and job definitions do not receive public invocation
  policies.
- S3 buckets use S3-managed encryption, versioning, and blocked public access.
- SQS queues do not receive public queue policies.
- Runtime Batch tasks use the task role created by infrastructure and should
  not receive long-lived AWS keys.

## Short AWS Test

For the lowest-cost AWS smoke test, use `configs/deployments/aws-smoke.yaml`.
It creates a separate `eml-transformer-smoke` stack, keeps schedules disabled,
caps Batch at 2 vCPUs, and configures GDELT for one file and five URL fetches.

```bash
eml_transformer config-validate --deployment configs/deployments/aws-smoke.yaml
eml_transformer deployment-matrix --deployment configs/deployments/aws-smoke.yaml
eml_transformer config-render \
  --deployment configs/deployments/aws-smoke.yaml \
  --output configs/generated/aws-smoke.runtime.yaml
cd infra/cdk
cdk synth -c deployment_config=configs/deployments/aws-smoke.yaml
cdk diff -c deployment_config=configs/deployments/aws-smoke.yaml
cdk deploy -c deployment_config=configs/deployments/aws-smoke.yaml
```

The dev config keeps schedules disabled, avoids NAT Gateway creation, disables
AWS GPU embedding jobs, and uses small Fargate job sizes. The deployment matrix
shows the capped smoke-test topology and runtime mappings, but it does not
calculate a monthly dollar estimate.

To test runtime without the full hourly workflow, submit one capped Batch job or
run one CLI command with small limits:

```bash
eml_transformer aws-start-service \
  --config configs/generated/aws-smoke.runtime.yaml \
  --service gdelt_discovery \
  --date today \
  --max-files 1
```

After testing, stop schedules if any were enabled and destroy the dev stack if
you do not need retained resources:

```bash
cd infra/cdk
cdk destroy -c deployment_config=configs/deployments/aws-smoke.yaml
```

S3 buckets and DynamoDB tables are retained by design to prevent accidental data
loss. Delete retained data manually only after confirming you no longer need it.
