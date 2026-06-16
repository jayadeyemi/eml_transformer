from __future__ import annotations

from typing import Any
import json
import re


def render_runtime_config_from_cfn_outputs(
    stack_name: str,
    region: str = "us-east-1",
    *,
    _cfn_client: Any | None = None,
) -> dict[str, Any]:
    if _cfn_client is None:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "config-render-from-outputs requires boto3. "
                "Install with: python -m pip install boto3"
            ) from exc
        _cfn_client = boto3.client("cloudformation", region_name=region)

    response = _cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])

    if not stacks:
        raise ValueError(f"CloudFormation stack not found: {stack_name!r}")

    raw_outputs = stacks[0].get("Outputs", [])
    outputs: dict[str, str] = {o["OutputKey"]: o["OutputValue"] for o in raw_outputs}
    runtime_env: dict[str, str] = {}
    runtime_env_str = outputs.get("RuntimeEnvironment")

    if runtime_env_str:
        try:
            runtime_env = json.loads(runtime_env_str)
        except (json.JSONDecodeError, TypeError):
            pass

    def _get(output_key: str, env_key: str, default: str = "") -> str:
        return outputs.get(output_key) or runtime_env.get(env_key) or default

    batch_job_definitions: dict[str, str] = {}
    for key, value in outputs.items():
        if key.endswith("JobDefinition") and value:
            service_pascal = key[: -len("JobDefinition")]
            service = _pascal_to_snake(service_pascal)
            batch_job_definitions[service] = value

    prefix = "BATCH_JOB_DEFINITION_"
    for env_key, env_value in runtime_env.items():
        if env_key.startswith(prefix) and env_value:
            service = env_key[len(prefix):].lower()
            batch_job_definitions.setdefault(service, env_value)

    env_region = _get("", "AWS_REGION", region)
    return {
        "storage": {
            "backend": "s3",
            "bucket": _get("DataBucketName", "DATA_BUCKET"),
            "prefix": runtime_env.get("STORAGE_PREFIX", ""),
            "region": env_region,
        },
        "aws": {
            "region": env_region,
            "environment": runtime_env.get("EML_ENVIRONMENT", "dev"),
            "infra_stack": runtime_env.get("INFRA_STACK", stack_name),
            "cdk_stack": runtime_env.get("CDK_STACK", stack_name),
            "project": runtime_env.get("PROJECT", "eml_transformer"),
            "cloudwatch_namespace": runtime_env.get(
                "CLOUDWATCH_NAMESPACE",
                "EMLTransformer/Collection",
            ),
        },
        "queues": {
            "url_fetch_queue_url": _get("UrlFetchQueueUrl", "URL_FETCH_QUEUE_URL"),
            "article_url_dlq_url": _get("UrlFetchDlqUrl", "ARTICLE_URL_DLQ_URL"),
        },
        "state": {
            "url_table": _get("UrlStateTable", "URL_STATE_TABLE"),
            "run_table": _get("RunStateTable", "RUN_STATE_TABLE"),
            "domain_throttle_table": _get("DomainThrottleTable", "DOMAIN_THROTTLE_TABLE"),
        },
        "orchestration": {
            "state_machine_arn": _get("StateMachineArn", "STATE_MACHINE_ARN"),
            "source_workflow_arn": _get("SourceWorkflowArn", "SOURCE_WORKFLOW_ARN"),
            "backfill_workflow_arn": _get("BackfillWorkflowArn", "BACKFILL_WORKFLOW_ARN"),
            "batch_job_queue": _get("BatchJobQueue", "BATCH_JOB_QUEUE"),
            "batch_job_definitions": batch_job_definitions,
        },
        "notifications": {
            "sns_topic_arn": _get("SnsTopicArn", "SNS_TOPIC_ARN") or None,
        },
    }


def _pascal_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
