import os
from pathlib import Path
from typing import Any

import yaml


def load_config(
    path: str | Path = "configs/dev.yaml",
) -> dict[str, Any]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg is None:
        raise ValueError(f"Config file is empty: {path}")

    apply_environment_overrides(cfg)

    return cfg


def apply_environment_overrides(cfg: dict[str, Any]) -> None:
    """
    Apply runtime overrides supplied by infrastructure/AWS Batch environments.

    This keeps committed YAML portable while allowing CDK, Terraform, or other
    deployment outputs to be injected into containers at runtime.
    """

    storage_cfg = cfg.setdefault("storage", {})
    aws_cfg = cfg.setdefault("aws", {})
    queue_cfg = cfg.setdefault("queues", {})
    state_cfg = cfg.setdefault("state", {})
    orchestration_cfg = cfg.setdefault("orchestration", {})

    _set_from_env(storage_cfg, "bucket", "DATA_BUCKET", "S3_BUCKET")
    _set_from_env(storage_cfg, "prefix", "STORAGE_PREFIX", "S3_PREFIX")
    _set_from_env(storage_cfg, "region", "AWS_REGION")
    # When DATA_BUCKET is injected (e.g. inside AWS Batch) automatically switch
    # to S3 backend so local-default configs work correctly in cloud containers.
    if os.getenv("DATA_BUCKET") or os.getenv("S3_BUCKET"):
        storage_cfg["backend"] = "s3"

    _set_from_env(aws_cfg, "region", "AWS_REGION")
    _set_from_env(aws_cfg, "environment", "EML_ENVIRONMENT")
    _set_from_env(aws_cfg, "infra_stack", "INFRA_STACK", "CDK_STACK")
    _set_from_env(aws_cfg, "cdk_stack", "CDK_STACK", "INFRA_STACK")
    _set_from_env(aws_cfg, "project", "PROJECT")
    _set_from_env(aws_cfg, "cloudwatch_namespace", "CLOUDWATCH_NAMESPACE")

    _set_from_env(
        queue_cfg,
        "url_fetch_queue_url",
        "URL_FETCH_QUEUE_URL",
        "ARTICLE_URL_QUEUE_URL",
    )
    _set_from_env(state_cfg, "url_table", "URL_STATE_TABLE")
    _set_from_env(state_cfg, "run_table", "RUN_STATE_TABLE")
    _set_from_env(state_cfg, "domain_throttle_table", "DOMAIN_THROTTLE_TABLE")

    _set_from_env(orchestration_cfg, "state_machine_arn", "STATE_MACHINE_ARN")
    _set_from_env(orchestration_cfg, "source_workflow_arn", "SOURCE_WORKFLOW_ARN")
    _set_from_env(orchestration_cfg, "backfill_workflow_arn", "BACKFILL_WORKFLOW_ARN")
    _set_from_env(orchestration_cfg, "batch_job_queue", "BATCH_JOB_QUEUE")
    _set_from_env(orchestration_cfg, "batch_job_definition", "BATCH_JOB_DEFINITION")

    batch_job_definitions = orchestration_cfg.setdefault("batch_job_definitions", {})
    prefix = "BATCH_JOB_DEFINITION_"

    for env_key, value in os.environ.items():
        if env_key.startswith(prefix) and value:
            service = env_key[len(prefix):].lower()
            batch_job_definitions[service] = value


def _set_from_env(target: dict[str, Any], key: str, *env_keys: str) -> None:
    for env_key in env_keys:
        value = os.getenv(env_key)

        if value:
            target[key] = value
            return


def build_source_config(
    source: str,
    cfg: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    sources_cfg = cfg.get("sources", {})

    if source not in sources_cfg:
        valid = ", ".join(sources_cfg.keys())

        raise ValueError(
            f"Unknown source: {source}. Available sources: {valid}"
        )

    source_cfg = dict(sources_cfg[source])

    source_cfg.pop("enabled", None)

    api_key_env = source_cfg.pop(
        "api_key_env",
        None,
    )

    if api_key_env:
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise EnvironmentError(
                f"Missing required environment variable: {api_key_env}"
            )

        source_cfg["api_key"] = api_key

    return source, source_cfg


def build_source_configs(
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    configs = {}

    for source_name, source_cfg in cfg.get(
        "sources",
        {},
    ).items():

        if not source_cfg.get(
            "enabled",
            True,
        ):
            continue

        name, kwargs = build_source_config(
            source_name,
            cfg,
        )

        configs[name] = kwargs

    return configs
