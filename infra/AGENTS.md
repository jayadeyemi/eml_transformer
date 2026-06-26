# AGENTS.md

## Optional Infrastructure

This directory contains optional infrastructure templates. The main pipeline must work with existing buckets and should not require Terraform, CDK, DataSync, or cloud infrastructure creation.

## Rules

- AWS infrastructure is optional.
- Do not create DataSync resources by default.
- Do not assume a specific infrastructure framework.
- S3 templates should use encryption, block public access, lifecycle cleanup for incomplete multipart uploads, least-privilege IAM policies scoped to specific handoff buckets/prefixes, and lifecycle expiration for project handoff objects as a backup to explicit workflow cleanup.
- Provide separate read-input and write-output policy examples when useful.
- Do not include credentials, access keys, private bucket names, account IDs, or personal AWS profiles.
- Treat CloudWatch, CloudTrail, lifecycle transitions, and DataSync as optional advanced features.
- DataSync support must be disabled unless the user explicitly configures an existing IU-supported path or task.
- Prototype CDK files from implementation branches can guide optional examples, but do not recreate `infra/cdk` unless the user explicitly asks for optional AWS infrastructure templates.
