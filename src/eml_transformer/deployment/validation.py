from __future__ import annotations

from typing import Any

from eml_transformer.deployment.model import COLLECTION_SERVICES


def validate_deployment_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    infra = cfg.get("infra", {})
    storage = cfg.get("storage", {})
    lifecycle = storage.get("lifecycle", {})
    services = cfg.get("services", {})
    notifications = cfg.get("notifications", {})
    runtime_secrets = cfg.get("runtime_secrets", {})

    _require_mapping(errors, cfg, "infra")
    _require_mapping(errors, cfg, "cost")
    _require_mapping(errors, cfg, "storage")
    _require_mapping(errors, cfg, "services")
    _require_mapping(errors, cfg, "sources")

    if infra.get("engine") not in {"cdk", "hpc", "local"}:
        errors.append("infra.engine must be one of: cdk, hpc, local")

    for key in ("stack_name", "region", "environment"):
        if not infra.get(key):
            errors.append(f"infra.{key} is required")

    if storage.get("backend") not in {"s3", "local"}:
        errors.append("storage.backend must be either s3 or local")

    if notifications and not isinstance(notifications, dict):
        errors.append("notifications must be a mapping")
    else:
        sns_cfg = notifications.get("sns", {}) if isinstance(notifications, dict) else {}
        if sns_cfg and not isinstance(sns_cfg, dict):
            errors.append("notifications.sns must be a mapping")
        elif sns_cfg:
            recipients = sns_cfg.get("email_recipients", [])
            if recipients is not None and not isinstance(recipients, list):
                errors.append("notifications.sns.email_recipients must be a list")
            for email in recipients or []:
                if not isinstance(email, str) or "@" not in email:
                    errors.append(
                        "notifications.sns.email_recipients entries must be email strings"
                    )

    if runtime_secrets and not isinstance(runtime_secrets, dict):
        errors.append("runtime_secrets must be a mapping")
    else:
        for secret_name, secret_cfg in runtime_secrets.items():
            if not isinstance(secret_cfg, dict):
                errors.append(f"runtime_secrets.{secret_name} must be a mapping")
                continue
            if not secret_cfg.get("secret_arn_env"):
                errors.append(f"runtime_secrets.{secret_name}.secret_arn_env is required")
            secret_services = secret_cfg.get("services", [])
            if not isinstance(secret_services, list) or not secret_services:
                errors.append(f"runtime_secrets.{secret_name}.services must be a non-empty list")
                continue
            invalid_services = [
                service for service in secret_services if service not in COLLECTION_SERVICES
            ]
            if invalid_services:
                errors.append(
                    f"runtime_secrets.{secret_name}.services contains unknown services: "
                    + ", ".join(invalid_services)
                )

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
            errors.append(f"services.{service_name}.compute.memory_mib must be at least 512")
        if timeout_seconds <= 0:
            errors.append(f"services.{service_name}.compute.timeout_seconds must be positive")

    return errors


def deployment_config_warnings(cfg: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    infra = cfg.get("infra", {})
    network = cfg.get("network", {})

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

    if infra.get("engine") == "cdk" and infra.get("environment") == "prod":
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

    return warnings


def assert_valid_deployment_config(cfg: dict[str, Any]) -> None:
    errors = validate_deployment_config(cfg)
    if errors:
        raise ValueError("Invalid deployment config:\n- " + "\n- ".join(errors))


def _require_mapping(errors: list[str], cfg: dict[str, Any], key: str) -> None:
    if not isinstance(cfg.get(key), dict):
        errors.append(f"{key} must be a mapping")
