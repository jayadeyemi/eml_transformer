from __future__ import annotations

from typing import Any
import os

from eml_transformer.deployment.model import COLLECTION_SERVICES


def render_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    infra = cfg["infra"]
    engine = infra.get("engine", "cdk")
    stack = infra["stack_name"]
    region = infra.get("region", "us-east-1")
    account_id = str(infra.get("account_id") or os.getenv("AWS_ACCOUNT_ID") or "123456789012")
    environment = infra.get("environment", "dev")
    storage = cfg.get("storage", {})
    sns_cfg = cfg.get("notifications", {}).get("sns", {})
    storage_backend = storage.get("backend", "s3")
    is_aws = engine == "cdk"
    bucket = storage.get("bucket") or (f"{stack}-data-{account_id}" if is_aws else None)
    sns_topic_name = sns_cfg.get("topic_name") or f"{stack}-notifications"
    sns_topic_arn = (
        f"arn:aws:sns:{region}:{account_id}:{sns_topic_name}"
        if is_aws and sns_cfg.get("enabled", False)
        else None
    )
    service_job_definitions = service_job_definition_arns(cfg)
    storage_runtime = (
        {"backend": "local", "base_dir": storage.get("base_dir", "data")}
        if storage_backend == "local"
        else {
            "backend": "s3",
            "bucket": bucket,
            "prefix": storage.get("prefix", ""),
            "region": region,
        }
    )

    return {
        "storage": storage_runtime,
        "aws": {
            "region": region,
            "environment": environment,
            "infra_stack": stack,
            "cdk_stack": stack if is_aws else None,
            "project": infra.get("project", "eml_transformer"),
            "cloudwatch_namespace": cfg.get("observability", {}).get(
                "cloudwatch_namespace",
                "EMLTransformer/Collection",
            ),
        },
        "queues": {
            "url_fetch_queue_url": (
                f"https://sqs.{region}.amazonaws.com/{account_id}/{stack}-url-fetch"
                if is_aws
                else cfg.get("queues", {}).get("url_fetch_queue_url")
            ),
            "article_url_dlq_url": (
                f"https://sqs.{region}.amazonaws.com/{account_id}/{stack}-url-fetch-dlq"
                if is_aws
                else cfg.get("queues", {}).get("article_url_dlq_url")
            ),
        },
        "state": {
            "url_table": f"{stack}-url-state" if is_aws else cfg.get("state", {}).get("url_table"),
            "run_table": f"{stack}-run-state" if is_aws else cfg.get("state", {}).get("run_table"),
            "domain_throttle_table": (
                f"{stack}-domain-throttle"
                if is_aws
                else cfg.get("state", {}).get("domain_throttle_table")
            ),
        },
        "orchestration": {
            "state_machine_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-acquisition"
                if is_aws
                else cfg.get("orchestration", {}).get("state_machine_arn")
            ),
            "source_workflow_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-source-workflow"
                if is_aws
                else cfg.get("orchestration", {}).get("source_workflow_arn")
            ),
            "backfill_workflow_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-backfill-workflow"
                if is_aws
                else cfg.get("orchestration", {}).get("backfill_workflow_arn")
            ),
            "batch_job_queue": (
                f"arn:aws:batch:{region}:{account_id}:job-queue/{stack}-collection"
                if is_aws
                else cfg.get("orchestration", {}).get("batch_job_queue")
            ),
            "batch_job_definitions": service_job_definitions,
        },
        "notifications": {"sns_topic_arn": sns_topic_arn},
        "paths": cfg.get("paths", {"root": "."}),
        "sources": cfg.get("sources", {}),
        "embeddings": cfg.get("embeddings", {}),
    }


def build_runtime_environment(cfg: dict[str, Any]) -> dict[str, str]:
    runtime = render_runtime_config(cfg)
    aws_cfg = runtime["aws"]
    storage_cfg = runtime["storage"]
    queue_cfg = runtime["queues"]
    state_cfg = runtime["state"]
    orchestration_cfg = runtime["orchestration"]
    notification_cfg = runtime.get("notifications", {})
    env = {
        "AWS_REGION": aws_cfg["region"] or "",
        "EML_ENVIRONMENT": aws_cfg["environment"] or "",
        "INFRA_STACK": aws_cfg["infra_stack"] or "",
        "CDK_STACK": aws_cfg.get("cdk_stack") or "",
        "DATA_BUCKET": storage_cfg.get("bucket", ""),
        "STORAGE_PREFIX": storage_cfg.get("prefix") or "",
        "URL_FETCH_QUEUE_URL": queue_cfg.get("url_fetch_queue_url") or "",
        "ARTICLE_URL_DLQ_URL": queue_cfg.get("article_url_dlq_url") or "",
        "URL_STATE_TABLE": state_cfg.get("url_table") or "",
        "RUN_STATE_TABLE": state_cfg.get("run_table") or "",
        "DOMAIN_THROTTLE_TABLE": state_cfg.get("domain_throttle_table") or "",
        "STATE_MACHINE_ARN": orchestration_cfg.get("state_machine_arn") or "",
        "SOURCE_WORKFLOW_ARN": orchestration_cfg.get("source_workflow_arn") or "",
        "BACKFILL_WORKFLOW_ARN": orchestration_cfg.get("backfill_workflow_arn") or "",
        "BATCH_JOB_QUEUE": orchestration_cfg.get("batch_job_queue") or "",
        "CLOUDWATCH_NAMESPACE": aws_cfg["cloudwatch_namespace"] or "",
    }
    if notification_cfg.get("sns_topic_arn"):
        env["SNS_TOPIC_ARN"] = notification_cfg["sns_topic_arn"]

    gdelt_acquisition = cfg.get("sources", {}).get("gdelt", {}).get("acquisition", {})
    if gdelt_acquisition.get("max_urls_per_run") is not None:
        env["GDELT_MAX_URLS_PER_RUN"] = str(gdelt_acquisition["max_urls_per_run"])

    for service, arn in orchestration_cfg.get("batch_job_definitions", {}).items():
        env[f"BATCH_JOB_DEFINITION_{service.upper()}"] = arn

    return env


def service_job_definition_arns(cfg: dict[str, Any]) -> dict[str, str]:
    infra = cfg["infra"]
    if infra.get("engine") != "cdk":
        return {}

    stack = infra["stack_name"]
    region = infra.get("region", "us-east-1")
    account_id = str(infra.get("account_id") or os.getenv("AWS_ACCOUNT_ID") or "123456789012")

    return {
        service: (
            f"arn:aws:batch:{region}:{account_id}:job-definition/"
            f"{stack}-{service.replace('_', '-')}"
        )
        for service in COLLECTION_SERVICES
    }
