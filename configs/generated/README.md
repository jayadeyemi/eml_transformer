# Generated Runtime Configs

Runtime YAML files in this directory are generated from layered deployment
configs and are intentionally ignored by git.

Render one before building or running AWS containers:

```bash
eml_transformer config-render \
  --deployment configs/deployments/aws-dev.yaml \
  --output configs/generated/aws-dev.runtime.yaml
```

Use `AWS_ACCOUNT_ID=<account-id>` only when you intentionally want real
account/resource metadata in the generated file.

After CDK deploy, render from live CloudFormation outputs and merge the
deployment's source/path settings:

```bash
eml_transformer config-render-from-outputs \
  --stack eml-transformer-dev \
  --region us-east-1 \
  --deployment configs/deployments/aws-dev.yaml \
  --output configs/generated/aws-dev.runtime.yaml
```
