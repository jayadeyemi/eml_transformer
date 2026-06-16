from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eml_transformer.deployment.config import (
    COLLECTION_SERVICES,
    deployment_metadata,
    load_deployment_config,
)


@dataclass(frozen=True)
class CleanupTarget:
    deployment: str
    stack_name: str
    region: str
    data_bucket: str
    ecr_repository: str
    dynamodb_tables: tuple[str, ...]
    batch_job_definition_names: tuple[str, ...]
    schedule_prefix: str
    log_group: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment": self.deployment,
            "stack_name": self.stack_name,
            "region": self.region,
            "data_bucket": self.data_bucket,
            "ecr_repository": self.ecr_repository,
            "dynamodb_tables": list(self.dynamodb_tables),
            "batch_job_definition_names": list(self.batch_job_definition_names),
            "schedule_prefix": self.schedule_prefix,
            "log_group": self.log_group,
        }


@dataclass(frozen=True)
class CleanupAction:
    action: str
    resource: str
    service: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "resource": self.resource,
            "service": self.service,
        }


def build_cleanup_target(
    deployment: str | Path,
    *,
    account_id: str = "123456789012",
) -> CleanupTarget:
    loaded = load_deployment_config(deployment)
    metadata = deployment_metadata(loaded.config)
    stack = str(metadata["stack_name"])
    region = str(metadata["region"])
    storage = loaded.config.get("storage", {})
    bucket = storage.get("bucket") or f"{stack}-data-{account_id}"

    return CleanupTarget(
        deployment=str(metadata["deployment_name"]),
        stack_name=stack,
        region=region,
        data_bucket=bucket,
        ecr_repository=f"{stack}-collection",
        dynamodb_tables=(
            f"{stack}-url-state",
            f"{stack}-run-state",
            f"{stack}-domain-throttle",
        ),
        batch_job_definition_names=tuple(
            f"{stack}-{service.replace('_', '-')}"
            for service in COLLECTION_SERVICES
        ),
        schedule_prefix=stack,
        log_group=f"/aws/batch/{stack}/collection",
    )


def plan_stack_cleanup(target: CleanupTarget) -> tuple[CleanupAction, ...]:
    actions = [
        CleanupAction("delete_stack", target.stack_name, "cloudformation"),
        CleanupAction("delete_schedules_by_prefix", target.schedule_prefix, "scheduler"),
        CleanupAction("empty_versioned_bucket", target.data_bucket, "s3"),
        CleanupAction("delete_bucket", target.data_bucket, "s3"),
        CleanupAction("delete_ecr_repository", target.ecr_repository, "ecr"),
        CleanupAction("delete_log_group", target.log_group, "logs"),
    ]
    actions.extend(
        CleanupAction("delete_dynamodb_table", table, "dynamodb")
        for table in target.dynamodb_tables
    )
    actions.extend(
        CleanupAction("deregister_batch_job_definition", name, "batch")
        for name in target.batch_job_definition_names
    )
    return tuple(actions)


def reset_stack_from_deployment(
    deployment: str | Path,
    *,
    confirm_stack: str,
    profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    account_id = _account_id(profile=profile)
    target = build_cleanup_target(deployment, account_id=account_id)
    if confirm_stack != target.stack_name:
        raise ValueError(
            f"Refusing destructive reset. Expected --confirm-stack {target.stack_name}."
        )

    actions = plan_stack_cleanup(target)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "target": target.to_dict(),
        "planned_actions": [action.to_dict() for action in actions],
        "executed_actions": [],
    }
    if dry_run:
        return result

    _execute_cleanup(target, profile=profile, result=result)
    return result


def _account_id(*, profile: str | None = None) -> str:
    boto3 = _boto3()
    session = boto3.Session(profile_name=profile or None)
    return session.client("sts").get_caller_identity()["Account"]


def _execute_cleanup(
    target: CleanupTarget,
    *,
    profile: str | None,
    result: dict[str, Any],
) -> None:
    boto3 = _boto3()
    session = boto3.Session(profile_name=profile or None, region_name=target.region)

    _delete_stack(session, target, result)
    _delete_schedules(session, target, result)
    _empty_and_delete_bucket(session, target, result)
    _delete_dynamodb_tables(session, target, result)
    _delete_ecr_repository(session, target, result)
    _deregister_job_definitions(session, target, result)
    _delete_log_group(session, target, result)


def _record(result: dict[str, Any], action: str, resource: str, status: str, **extra: Any) -> None:
    result["executed_actions"].append(
        {"action": action, "resource": resource, "status": status, **extra}
    )


def _delete_stack(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    cfn = session.client("cloudformation")
    try:
        cfn.describe_stacks(StackName=target.stack_name)
    except Exception as exc:
        _record(result, "delete_stack", target.stack_name, "skipped", error=str(exc))
        return

    cfn.delete_stack(StackName=target.stack_name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=target.stack_name)
    _record(result, "delete_stack", target.stack_name, "deleted")


def _delete_schedules(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    scheduler = session.client("scheduler")
    try:
        schedules = scheduler.list_schedules(NamePrefix=target.schedule_prefix).get("Schedules", [])
    except Exception as exc:
        _record(result, "delete_schedules_by_prefix", target.schedule_prefix, "error", error=str(exc))
        return

    for schedule in schedules:
        name = schedule["Name"]
        scheduler.delete_schedule(Name=name)
        _record(result, "delete_schedule", name, "deleted")


def _empty_and_delete_bucket(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=target.data_bucket)
    except Exception as exc:
        _record(result, "delete_bucket", target.data_bucket, "skipped", error=str(exc))
        return

    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=target.data_bucket):
        objects = []
        for section in ("Versions", "DeleteMarkers"):
            for item in page.get(section, []) or []:
                objects.append({"Key": item["Key"], "VersionId": item["VersionId"]})
        for start in range(0, len(objects), 1000):
            s3.delete_objects(
                Bucket=target.data_bucket,
                Delete={"Objects": objects[start : start + 1000], "Quiet": True},
            )
    _record(result, "empty_versioned_bucket", target.data_bucket, "emptied")

    try:
        s3.delete_bucket(Bucket=target.data_bucket)
        _record(result, "delete_bucket", target.data_bucket, "deleted")
    except Exception as exc:
        _record(result, "delete_bucket", target.data_bucket, "error", error=str(exc))


def _delete_dynamodb_tables(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    dynamodb = session.client("dynamodb")
    for table in target.dynamodb_tables:
        try:
            dynamodb.describe_table(TableName=table)
        except Exception as exc:
            _record(result, "delete_dynamodb_table", table, "skipped", error=str(exc))
            continue
        dynamodb.delete_table(TableName=table)
        dynamodb.get_waiter("table_not_exists").wait(TableName=table)
        _record(result, "delete_dynamodb_table", table, "deleted")


def _delete_ecr_repository(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    ecr = session.client("ecr")
    try:
        ecr.delete_repository(repositoryName=target.ecr_repository, force=True)
        _record(result, "delete_ecr_repository", target.ecr_repository, "deleted")
    except Exception as exc:
        _record(result, "delete_ecr_repository", target.ecr_repository, "skipped", error=str(exc))


def _deregister_job_definitions(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    batch = session.client("batch")
    for name in target.batch_job_definition_names:
        try:
            response = batch.describe_job_definitions(
                jobDefinitionName=name,
                status="ACTIVE",
            )
        except Exception as exc:
            _record(result, "deregister_batch_job_definition", name, "skipped", error=str(exc))
            continue
        for definition in response.get("jobDefinitions", []):
            arn = definition["jobDefinitionArn"]
            batch.deregister_job_definition(jobDefinition=arn)
            _record(result, "deregister_batch_job_definition", arn, "deregistered")


def _delete_log_group(session: Any, target: CleanupTarget, result: dict[str, Any]) -> None:
    logs = session.client("logs")
    try:
        logs.delete_log_group(logGroupName=target.log_group)
        _record(result, "delete_log_group", target.log_group, "deleted")
    except Exception as exc:
        _record(result, "delete_log_group", target.log_group, "skipped", error=str(exc))


def _boto3() -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "AWS cleanup requires boto3. Install with `python -m pip install -e .[aws]`."
        ) from exc
    return boto3
