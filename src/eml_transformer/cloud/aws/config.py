from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AwsRuntimeConfig:
    region: str
    environment: str
    infra_stack: str | None = None
    cdk_stack: str | None = None
    project: str = "eml_transformer"
    aws_profile: str | None = None
    url_fetch_queue_url: str | None = None
    url_state_table: str | None = None
    run_state_table: str | None = None
    domain_throttle_table: str | None = None
    state_machine_arn: str | None = None
    source_workflow_arn: str | None = None
    backfill_workflow_arn: str | None = None
    batch_job_queue: str | None = None
    batch_job_definitions: dict[str, str] = field(default_factory=dict)
    sns_topic_arn: str | None = None
    gdelt_max_urls_per_run: int | None = None
    cloudwatch_namespace: str = "EMLTransformer/Collection"

    @property
    def base_tags(self) -> dict[str, str]:
        return {
            "project": self.project,
            "environment": self.environment,
            "infra_stack": self.infra_stack or "",
            "cdk_stack": self.cdk_stack or "",
        }

    def job_definition_for(self, service: str) -> str | None:
        normalized = normalize_service_name(service)
        return self.batch_job_definitions.get(normalized)


def normalize_service_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _env_or_cfg(env_key: str, cfg_value: Any, default: Any = None) -> Any:
    return os.getenv(env_key) or cfg_value or default


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None

    return int(value)


def _load_batch_job_definitions(cfg: dict[str, Any]) -> dict[str, str]:
    definitions = {
        normalize_service_name(key): value
        for key, value in cfg.get("batch_job_definitions", {}).items()
        if value
    }

    for key, value in os.environ.items():
        prefix = "BATCH_JOB_DEFINITION_"

        if key.startswith(prefix) and value:
            service = normalize_service_name(key[len(prefix) :])
            definitions[service] = value

    return definitions


def load_aws_runtime_config(cfg: dict[str, Any]) -> AwsRuntimeConfig:
    aws_cfg = cfg.get("aws", {})
    queue_cfg = cfg.get("queues", {})
    state_cfg = cfg.get("state", {})
    orchestration_cfg = cfg.get("orchestration", {})
    notification_cfg = cfg.get("notifications", {})
    gdelt_acquisition_cfg = (
        cfg.get("sources", {})
        .get("gdelt", {})
        .get("acquisition", {})
    )

    region = _env_or_cfg("AWS_REGION", aws_cfg.get("region"), "us-east-1")
    environment = _env_or_cfg(
        "EML_ENVIRONMENT",
        aws_cfg.get("environment"),
        "dev",
    )
    infra_stack = _env_or_cfg(
        "INFRA_STACK",
        aws_cfg.get("infra_stack"),
        _env_or_cfg("CDK_STACK", aws_cfg.get("cdk_stack")),
    )
    cdk_stack = _env_or_cfg("CDK_STACK", aws_cfg.get("cdk_stack"), infra_stack)

    return AwsRuntimeConfig(
        region=region,
        environment=environment,
        infra_stack=infra_stack,
        cdk_stack=cdk_stack,
        project=_env_or_cfg("PROJECT", aws_cfg.get("project"), "eml_transformer"),
        aws_profile=_env_or_cfg("AWS_PROFILE", aws_cfg.get("profile")),
        url_fetch_queue_url=_env_or_cfg(
            "URL_FETCH_QUEUE_URL",
            queue_cfg.get("url_fetch_queue_url"),
        ),
        url_state_table=_env_or_cfg("URL_STATE_TABLE", state_cfg.get("url_table")),
        run_state_table=_env_or_cfg("RUN_STATE_TABLE", state_cfg.get("run_table")),
        domain_throttle_table=_env_or_cfg(
            "DOMAIN_THROTTLE_TABLE",
            state_cfg.get("domain_throttle_table"),
        ),
        state_machine_arn=_env_or_cfg(
            "STATE_MACHINE_ARN",
            orchestration_cfg.get("state_machine_arn"),
        ),
        source_workflow_arn=_env_or_cfg(
            "SOURCE_WORKFLOW_ARN",
            orchestration_cfg.get("source_workflow_arn"),
        ),
        backfill_workflow_arn=_env_or_cfg(
            "BACKFILL_WORKFLOW_ARN",
            orchestration_cfg.get("backfill_workflow_arn"),
        ),
        batch_job_queue=_env_or_cfg(
            "BATCH_JOB_QUEUE",
            orchestration_cfg.get("batch_job_queue"),
        ),
        batch_job_definitions=_load_batch_job_definitions(orchestration_cfg),
        sns_topic_arn=_env_or_cfg(
            "SNS_TOPIC_ARN",
            notification_cfg.get("sns_topic_arn"),
        ),
        gdelt_max_urls_per_run=_optional_int(
            _env_or_cfg(
                "GDELT_MAX_URLS_PER_RUN",
                gdelt_acquisition_cfg.get("max_urls_per_run"),
            )
        ),
        cloudwatch_namespace=_env_or_cfg(
            "CLOUDWATCH_NAMESPACE",
            aws_cfg.get("cloudwatch_namespace"),
            "EMLTransformer/Collection",
        ),
    )
