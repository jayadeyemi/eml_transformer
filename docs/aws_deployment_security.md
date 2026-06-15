# AWS Deployment Security And Cost Notes

Generated runtime configs, CloudFormation outputs, and account/resource
metadata should not be committed to a public repository. Runtime examples under
`configs/generated_examples/` use fake account IDs and ARNs only.

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
- SNS topics are private account resources; email subscribers must confirm the
  AWS subscription email before messages are delivered.
- Runtime Batch tasks use the task role created by infrastructure and should
  not receive long-lived AWS keys.
- Source API keys for AWS Batch are injected from Secrets Manager by ARN. The
  smoke deployment expects `NEWSAPI_SECRET_NAME` to identify an operator-created
  secret whose value is the raw NewsAPI key.

## Short AWS Test

For the lowest-cost AWS smoke test, use `configs/deployments/aws-smoke.yaml`.
It creates a separate `eml-transformer-smoke` stack, caps Batch at 2 vCPUs,
configures GDELT for one file and five URL fetches, and enables accelerated
test schedules plus SNS notifications.

```bash
export AWS_PROFILE=episb
export NEWSAPI_SECRET_NAME=<operator-created-secret-name>
export CONFIRM_STACK_DELETE=eml-transformer-smoke
bash scripts/aws_test/run_all.sh --reset-stack
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

The full smoke runner disables accelerated schedules after diagnostics. To
destroy the stack and delete retained artifacts again:

```bash
CONFIRM_STACK_DELETE=eml-transformer-smoke \
  bash scripts/aws_test/reset_smoke_stack.sh
```

S3 buckets, DynamoDB tables, and ECR repositories are retained by design to
prevent accidental data loss. The guarded reset script deletes those retained
artifacts explicitly for the smoke stack.
