from __future__ import annotations

from typing import Any

from eml_transformer.deployment.rendering import build_runtime_environment


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
                "schedule_enabled": bool(service.get("schedule", {}).get("enabled", False)),
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


def deployment_name(cfg: dict[str, Any]) -> str:
    return (
        cfg.get("deployment", {}).get("name")
        or cfg.get("infra", {}).get("stack_name")
        or "deployment"
    )


def runtime_config_path(cfg: dict[str, Any]) -> str:
    return (
        cfg.get("runtime", {}).get("config_path")
        or f"configs/generated/{deployment_name(cfg)}.runtime.yaml"
    )


def cfn_outputs_path(cfg: dict[str, Any]) -> str:
    return (
        cfg.get("runtime", {}).get("cfn_outputs_path")
        or f"configs/generated/{deployment_name(cfg)}.cfn-outputs.json"
    )


def deployment_metadata(cfg: dict[str, Any]) -> dict[str, Any]:
    infra = cfg.get("infra", {})
    return {
        "deployment_name": deployment_name(cfg),
        "engine": infra.get("engine"),
        "environment": infra.get("environment"),
        "is_prod": infra.get("environment") == "prod",
        "stack_name": infra.get("stack_name"),
        "region": infra.get("region", "us-east-1"),
        "runtime_config_path": runtime_config_path(cfg),
        "cfn_outputs_path": cfn_outputs_path(cfg),
    }
