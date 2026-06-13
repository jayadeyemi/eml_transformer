from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

import yaml


COLLECTION_SERVICES = [
    "ingest",
    "standardize",
    "embed",
    "backfill",
    "run_all",
    "gdelt_discovery",
    "url_fetch_worker",
    "s3_restore_operator",
]


@dataclass(frozen=True)
class DeploymentConfig:
    path: Path
    config: dict[str, Any]
    layers: list[Path]


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level: {path}")

    return data


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)

    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)

    return merged


def load_deployment_config(path: str | Path) -> DeploymentConfig:
    deployment_path = Path(path).resolve()
    deployment_doc = load_yaml(deployment_path)
    repo_root = _find_repo_root(deployment_path)
    deployment_meta = deployment_doc.get("deployment", {})
    layers: list[Path] = []
    merged: dict[str, Any] = {}

    base_path = _resolve_config_path(
        deployment_meta.get("base_config", "configs/base.yaml"),
        repo_root,
        deployment_path.parent,
    )
    layers.append(base_path)
    merged = deep_merge(merged, load_yaml(base_path))

    environment_config = deployment_meta.get("environment_config")

    if environment_config:
        environment_path = _resolve_config_path(
            environment_config,
            repo_root,
            deployment_path.parent,
        )
    else:
        environment_name = (
            deployment_doc.get("infra", {}).get("environment")
            or merged.get("infra", {}).get("environment")
            or "dev"
        )
        environment_path = repo_root / "configs" / "environments" / f"{environment_name}.yaml"

    if environment_path.exists():
        layers.append(environment_path)
        merged = deep_merge(merged, load_yaml(environment_path))

    source_configs = deployment_meta.get("source_configs")

    if source_configs is None:
        source_paths = sorted((repo_root / "configs" / "sources").glob("*.yaml"))
    else:
        source_paths = [
            _resolve_config_path(item, repo_root, deployment_path.parent)
            for item in source_configs
        ]

    for source_path in source_paths:
        layers.append(source_path)
        merged = deep_merge(merged, load_yaml(source_path))

    layers.append(deployment_path)
    merged = deep_merge(merged, deployment_doc)

    return DeploymentConfig(
        path=deployment_path,
        config=merged,
        layers=layers,
    )


def validate_deployment_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    infra = cfg.get("infra", {})
    storage = cfg.get("storage", {})
    lifecycle = storage.get("lifecycle", {})
    services = cfg.get("services", {})

    _require_mapping(errors, cfg, "infra")
    _require_mapping(errors, cfg, "cost")
    _require_mapping(errors, cfg, "storage")
    _require_mapping(errors, cfg, "services")
    _require_mapping(errors, cfg, "sources")

    if infra.get("engine") not in {"cdk", "terraform", "hpc", "local"}:
        errors.append("infra.engine must be one of: cdk, terraform, hpc, local")

    for key in ("stack_name", "region", "environment"):
        if not infra.get(key):
            errors.append(f"infra.{key} is required")

    if storage.get("backend") not in {"s3", "local"}:
        errors.append("storage.backend must be either s3 or local")

    glacier_days = lifecycle.get("bronze_glacier_ir_days")
    deep_days = lifecycle.get("bronze_deep_archive_days")

    if glacier_days is not None and deep_days is not None and deep_days <= glacier_days:
        errors.append(
            "storage.lifecycle.bronze_deep_archive_days must be greater than "
            "storage.lifecycle.bronze_glacier_ir_days"
        )

    enabled_services = [
        name for name, service in services.items()
        if isinstance(service, dict) and service.get("enabled", False)
    ]

    if infra.get("engine") == "cdk" and not enabled_services:
        errors.append("At least one service must be enabled for a CDK deployment")

    # Fail fast on placeholder network values when a real account is supplied.
    if infra.get("engine") == "cdk" and infra.get("account_id"):
        network = cfg.get("network", {})
        placeholder_subnets = [
            s for s in network.get("subnet_ids", []) if "replace-me" in str(s)
        ]
        placeholder_sgs = [
            s for s in network.get("security_group_ids", []) if "replace-me" in str(s)
        ]
        if placeholder_subnets:
            errors.append(
                "network.subnet_ids contains placeholder values. "
                "Replace with real VPC subnet IDs before deploying."
            )
        if placeholder_sgs:
            errors.append(
                "network.security_group_ids contains placeholder values. "
                "Replace with real security group IDs before deploying."
            )

    # Production deployments with a real account_id must have alert emails.
    if infra.get("environment") == "prod" and infra.get("account_id"):
        if not cfg.get("cost", {}).get("alert_emails"):
            errors.append(
                "cost.alert_emails must not be empty for a production deployment."
            )

    for service_name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"services.{service_name} must be a mapping")
            continue

        compute = service.get("compute", {})
        vcpu = compute.get("vcpu", 1)
        memory_mib = compute.get("memory_mib", 2048)
        timeout_seconds = compute.get("timeout_seconds", 3600)

        if vcpu <= 0:
            errors.append(f"services.{service_name}.compute.vcpu must be positive")
        if memory_mib < 512:
            errors.append(
                f"services.{service_name}.compute.memory_mib must be at least 512"
            )
        if timeout_seconds <= 0:
            errors.append(
                f"services.{service_name}.compute.timeout_seconds must be positive"
            )

    return errors


def deployment_config_warnings(cfg: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    aws_cfg = cfg.get("aws", {})
    orchestration_cfg = cfg.get("orchestration", {})
    batch_job_definitions = orchestration_cfg.get("batch_job_definitions", {})
    infra = cfg.get("infra", {})
    network = cfg.get("network", {})

    # Warn about placeholder network values (error only when account_id is set).
    if infra.get("engine") == "cdk":
        if any("replace-me" in str(s) for s in network.get("subnet_ids", [])):
            warnings.append(
                "network.subnet_ids contains placeholder values. "
                "Replace with real VPC subnet IDs for any real AWS deployment."
            )
        if any("replace-me" in str(s) for s in network.get("security_group_ids", [])):
            warnings.append(
                "network.security_group_ids contains placeholder values. "
                "Replace with real security group IDs for any real AWS deployment."
            )

    # Warn about production readiness gaps when account_id is absent.
    if infra.get("environment") == "prod":
        if not infra.get("account_id"):
            warnings.append(
                "infra.account_id is not set for a production deployment. "
                "Set account_id or supply AWS_ACCOUNT_ID before deploying."
            )
        if not cfg.get("cost", {}).get("alert_emails"):
            warnings.append(
                "cost.alert_emails is empty for a production deployment. "
                "Add alert emails to receive cost and error notifications."
            )

    if aws_cfg.get("terraform_stack") and not aws_cfg.get("infra_stack"):
        warnings.append(
            "aws.terraform_stack is a compatibility alias. Prefer "
            "aws.infra_stack for new runtime configs."
        )

    if orchestration_cfg.get("batch_job_definition"):
        warnings.append(
            "orchestration.batch_job_definition is deprecated. Prefer "
            "orchestration.batch_job_definitions.<service>."
        )

    if isinstance(batch_job_definitions, dict) and batch_job_definitions.get(
        "collection_service"
    ):
        warnings.append(
            "orchestration.batch_job_definitions.collection_service is "
            "deprecated. Prefer service-specific job definitions."
        )

    return warnings


def assert_valid_deployment_config(cfg: dict[str, Any]) -> None:
    errors = validate_deployment_config(cfg)

    if errors:
        raise ValueError("Invalid deployment config:\n- " + "\n- ".join(errors))


def render_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    infra = cfg["infra"]
    stack = infra["stack_name"]
    region = infra.get("region", "us-east-1")
    account_id = str(
        infra.get("account_id")
        or os.getenv("AWS_ACCOUNT_ID")
        or "123456789012"
    )
    environment = infra.get("environment", "dev")
    storage = cfg.get("storage", {})
    storage_backend = storage.get("backend", "s3")
    bucket = storage.get("bucket") or f"{stack}-data-{account_id}"
    service_job_definitions = service_job_definition_arns(cfg)
    storage_runtime = (
        {
            "backend": "local",
            "base_dir": storage.get("base_dir", "data"),
        }
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
            "cdk_stack": stack if infra.get("engine") == "cdk" else None,
            "project": infra.get("project", "eml_transformer"),
            "cloudwatch_namespace": cfg.get("observability", {}).get(
                "cloudwatch_namespace",
                "EMLTransformer/Collection",
            ),
        },
        "queues": {
            "url_fetch_queue_url": (
                f"https://sqs.{region}.amazonaws.com/{account_id}/{stack}-url-fetch"
            ),
            "article_url_dlq_url": (
                f"https://sqs.{region}.amazonaws.com/{account_id}/{stack}-url-fetch-dlq"
            ),
        },
        "state": {
            "url_table": f"{stack}-url-state",
            "run_table": f"{stack}-run-state",
            "domain_throttle_table": f"{stack}-domain-throttle",
        },
        "orchestration": {
            "state_machine_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-acquisition"
            ),
            "source_workflow_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-source-workflow"
            ),
            "backfill_workflow_arn": (
                f"arn:aws:states:{region}:{account_id}:stateMachine:{stack}-backfill-workflow"
            ),
            "batch_job_queue": (
                f"arn:aws:batch:{region}:{account_id}:job-queue/{stack}-collection"
            ),
            "batch_job_definitions": service_job_definitions,
        },
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
    env = {
        "AWS_REGION": aws_cfg["region"],
        "EML_ENVIRONMENT": aws_cfg["environment"],
        "INFRA_STACK": aws_cfg["infra_stack"],
        "CDK_STACK": aws_cfg.get("cdk_stack") or "",
        "DATA_BUCKET": storage_cfg.get("bucket", ""),
        "STORAGE_PREFIX": storage_cfg.get("prefix") or "",
        "URL_FETCH_QUEUE_URL": queue_cfg["url_fetch_queue_url"],
        "URL_STATE_TABLE": state_cfg["url_table"],
        "RUN_STATE_TABLE": state_cfg["run_table"],
        "DOMAIN_THROTTLE_TABLE": state_cfg["domain_throttle_table"],
        "STATE_MACHINE_ARN": orchestration_cfg["state_machine_arn"],
        "SOURCE_WORKFLOW_ARN": orchestration_cfg["source_workflow_arn"],
        "BACKFILL_WORKFLOW_ARN": orchestration_cfg["backfill_workflow_arn"],
        "BATCH_JOB_QUEUE": orchestration_cfg["batch_job_queue"],
        "CLOUDWATCH_NAMESPACE": aws_cfg["cloudwatch_namespace"],
    }
    gdelt_acquisition = cfg.get("sources", {}).get("gdelt", {}).get("acquisition", {})

    if gdelt_acquisition.get("max_urls_per_run") is not None:
        env["GDELT_MAX_URLS_PER_RUN"] = str(gdelt_acquisition["max_urls_per_run"])

    for service, arn in orchestration_cfg["batch_job_definitions"].items():
        env[f"BATCH_JOB_DEFINITION_{service.upper()}"] = arn

    return env


def service_job_definition_arns(cfg: dict[str, Any]) -> dict[str, str]:
    infra = cfg["infra"]
    stack = infra["stack_name"]
    region = infra.get("region", "us-east-1")
    account_id = str(
        infra.get("account_id")
        or os.getenv("AWS_ACCOUNT_ID")
        or "123456789012"
    )

    return {
        service: (
            f"arn:aws:batch:{region}:{account_id}:job-definition/"
            f"{stack}-{service.replace('_', '-')}"
        )
        for service in COLLECTION_SERVICES
    }


def deployment_matrix(cfg: dict[str, Any]) -> dict[str, Any]:
    env = build_runtime_environment(cfg)
    services = []

    for name, service in sorted(cfg.get("services", {}).items()):
        if not isinstance(service, dict):
            continue

        compute = service.get("compute", {})
        services.append(
            {
                "service": name,
                "enabled": bool(service.get("enabled", False)),
                "source": service.get("source", "all"),
                "vcpu": compute.get("vcpu", 1),
                "memory_mib": compute.get("memory_mib", 2048),
                "timeout_seconds": compute.get("timeout_seconds", 3600),
                "schedule_enabled": bool(
                    service.get("schedule", {}).get("enabled", False)
                ),
                "schedule_expression": service.get("schedule", {}).get("expression"),
                "job_definition_env_key": f"BATCH_JOB_DEFINITION_{name.upper()}",
                "job_definition_arn": env.get(f"BATCH_JOB_DEFINITION_{name.upper()}"),
            }
        )

    sources = [
        {"source": name, "enabled": bool(source.get("enabled", True))}
        for name, source in sorted(cfg.get("sources", {}).items())
        if isinstance(source, dict)
    ]

    return {
        "infra": cfg.get("infra", {}),
        "services": services,
        "sources": sources,
        "runtime_environment": env,
    }


def _require_mapping(errors: list[str], cfg: dict[str, Any], key: str) -> None:
    if not isinstance(cfg.get(key), dict):
        errors.append(f"{key} must be a mapping")


def _resolve_config_path(value: str, repo_root: Path, relative_to: Path) -> Path:
    path = Path(value)

    if path.is_absolute():
        return path

    repo_path = (repo_root / path).resolve()

    if repo_path.exists():
        return repo_path

    return (relative_to / path).resolve()


def _find_repo_root(start: Path) -> Path:
    cursor = start if start.is_dir() else start.parent

    for candidate in [cursor, *cursor.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate

    return cursor


def render_runtime_config_from_cfn_outputs(
    stack_name: str,
    region: str = "us-east-1",
    *,
    _cfn_client: Any | None = None,
) -> dict[str, Any]:
    """Build a runtime config dict from an already-deployed CloudFormation stack's outputs.

    This is the Phase 2 authoritative path: real ARNs come from the deployed
    stack rather than deterministic name predictions.

    Args:
        stack_name: CloudFormation stack name.
        region: AWS region.
        _cfn_client: Optional pre-built CloudFormation client, used in tests.
    """
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

    # Collect per-service Batch job definition ARNs from CloudFormation outputs.
    batch_job_definitions: dict[str, str] = {}

    for key, value in outputs.items():
        if key.endswith("JobDefinition") and value:
            service_pascal = key[: -len("JobDefinition")]
            service = _pascal_to_snake(service_pascal)
            batch_job_definitions[service] = value

    # Fill in any missing entries from the RuntimeEnvironment env-var map.
    prefix = "BATCH_JOB_DEFINITION_"
    for env_key, env_value in runtime_env.items():
        if env_key.startswith(prefix) and env_value:
            service = env_key[len(prefix):].lower()
            batch_job_definitions.setdefault(service, env_value)

    env_region = _get("", "AWS_REGION", region)
    bucket = _get("DataBucketName", "DATA_BUCKET")
    queue_url = _get("UrlFetchQueueUrl", "URL_FETCH_QUEUE_URL")
    dlq_url = _get("UrlFetchDlqUrl", "ARTICLE_URL_DLQ_URL")
    url_state = _get("UrlStateTable", "URL_STATE_TABLE")
    run_state = _get("RunStateTable", "RUN_STATE_TABLE")
    domain_throttle = _get("DomainThrottleTable", "DOMAIN_THROTTLE_TABLE")
    job_queue = _get("BatchJobQueue", "BATCH_JOB_QUEUE")
    state_machine_arn = _get("StateMachineArn", "STATE_MACHINE_ARN")
    source_workflow_arn = _get("SourceWorkflowArn", "SOURCE_WORKFLOW_ARN")
    backfill_workflow_arn = _get("BackfillWorkflowArn", "BACKFILL_WORKFLOW_ARN")
    infra_stack = runtime_env.get("INFRA_STACK", stack_name)
    cdk_stack = runtime_env.get("CDK_STACK", stack_name)
    environment = runtime_env.get("EML_ENVIRONMENT", "dev")
    project = runtime_env.get("PROJECT", "eml_transformer")
    cloudwatch_namespace = runtime_env.get(
        "CLOUDWATCH_NAMESPACE", "EMLTransformer/Collection"
    )

    return {
        "storage": {
            "backend": "s3",
            "bucket": bucket,
            "prefix": runtime_env.get("STORAGE_PREFIX", ""),
            "region": env_region,
        },
        "aws": {
            "region": env_region,
            "environment": environment,
            "infra_stack": infra_stack,
            "cdk_stack": cdk_stack,
            "project": project,
            "cloudwatch_namespace": cloudwatch_namespace,
        },
        "queues": {
            "url_fetch_queue_url": queue_url,
            "article_url_dlq_url": dlq_url,
        },
        "state": {
            "url_table": url_state,
            "run_table": run_state,
            "domain_throttle_table": domain_throttle,
        },
        "orchestration": {
            "state_machine_arn": state_machine_arn,
            "source_workflow_arn": source_workflow_arn,
            "backfill_workflow_arn": backfill_workflow_arn,
            "batch_job_queue": job_queue,
            "batch_job_definitions": batch_job_definitions,
        },
    }


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
