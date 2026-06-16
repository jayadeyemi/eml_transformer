from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from eml_transformer.deployment.aws_cleanup import reset_stack_from_deployment
from eml_transformer.deployment.config import deployment_metadata, load_deployment_config


def find_repo_root(start: str | Path | None = None) -> Path:
    cursor = Path(start or Path.cwd()).resolve()
    if cursor.is_file():
        cursor = cursor.parent

    for candidate in [cursor, *cursor.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate

    return cursor


def repo_relative_path(path: str | Path, repo_root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def build_validation_context(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    root = find_repo_root(repo_root or Path.cwd())
    deployment_path = (
        Path(deployment).resolve()
        if Path(deployment).is_absolute()
        else (root / deployment).resolve()
    )
    loaded = load_deployment_config(deployment_path)
    metadata = deployment_metadata(loaded.config)
    deployment_name = str(metadata["deployment_name"])
    image_version_tag = os.getenv("IMAGE_VERSION_TAG", deployment_name)

    return {
        "repo_root": root,
        "deployment_path": deployment_path,
        "deployment_config": repo_relative_path(deployment_path, root),
        "deployment_name": deployment_name,
        "stack_name": str(metadata["stack_name"]),
        "region": str(metadata["region"]),
        "runtime_config": str(metadata["runtime_config_path"]),
        "cfn_outputs": str(metadata["cfn_outputs_path"]),
        "results_dir": Path(results_dir or root / "artifacts" / "aws_test_results").resolve(),
        "profile": profile or os.getenv("AWS_PROFILE", "episb"),
        "image_version_tag": image_version_tag,
        "image_tag": os.getenv("IMAGE_TAG", f"eml-transformer:{image_version_tag}"),
        "cdk_test_image_tag": os.getenv(
            "CDK_TEST_IMAGE_TAG",
            f"eml-transformer-cdk-test:{image_version_tag}",
        ),
        "build_extras": os.getenv("BUILD_EXTRAS", "aws,test"),
        "config": loaded.config,
    }


def context_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in context.items()
        if key != "config"
    }


def run_logged(
    name: str,
    argv: list[str],
    *,
    context: dict[str, Any],
    log_name: str,
    action: str,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    allow_failure: bool = False,
    shell: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo_root = Path(context["repo_root"])
    log_path = Path(context["results_dir"]) / action / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    merged_env.update(_base_env(context))
    if env:
        merged_env.update(env)
    merged_env.setdefault("PYTHONPATH", str(repo_root / "src"))
    argv = [_expand_env_refs(arg, merged_env) for arg in argv]

    printable = argv[0] if shell else " ".join(argv)
    payload = {
        "name": name,
        "argv": argv,
        "cwd": str((repo_root / cwd).resolve() if cwd else repo_root),
        "log_path": str(log_path),
        "dry_run": dry_run,
        "exit_code": 0,
        "ok": True,
    }

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"$ {printable}\n")
        if dry_run:
            log_file.write("dry_run=true\n")
            return payload

        process = subprocess.Popen(
            argv[0] if shell else argv,
            cwd=payload["cwd"],
            env=merged_env,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output_parts: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log_file.write(line)
            output_parts.append(line)
        exit_code = process.wait()

    if allow_failure and exit_code != 0:
        exit_code = 0

    payload["exit_code"] = exit_code
    payload["ok"] = exit_code == 0
    payload["output"] = "".join(output_parts)
    return payload


def aws_preflight(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _prepare_preflight_environment(context, dry_run=dry_run)
    account_ref = "${AWS_ACCOUNT_ID}"
    cdk_python = _cdk_python(context)
    cdk_app = f'"{cdk_python}" app.py'
    ecr_repo = (
        f"{account_ref}.dkr.ecr.{context['region']}.amazonaws.com/"
        f"{context['stack_name']}-collection"
    )
    commands = [
        (
            "docker-build-runtime",
            [
                "docker",
                "build",
                "--build-arg",
                f"OPTIONAL_EXTRAS={context['build_extras']}",
                "-t",
                context["image_tag"],
                str(context["repo_root"]),
            ],
            "docker_build.log",
            None,
            False,
        ),
        (
            "install-cdk-python-deps",
            [str(cdk_python), "-m", "pip", "install", "-q", "-r", "requirements.txt"],
            "cdk_python_deps.log",
            "infra/cdk",
            False,
        ),
        (
            "cdk-bootstrap",
            [
                "cdk",
                "bootstrap",
                f"aws://{account_ref}/{context['region']}",
                "--app",
                cdk_app,
                "--profile",
                context["profile"],
                "-c",
                f"deployment_config={context['deployment_config']}",
            ],
            "cdk_bootstrap.log",
            "infra/cdk",
            False,
        ),
        (
            "cdk-deploy",
            [
                "cdk",
                "deploy",
                context["stack_name"],
                "--app",
                cdk_app,
                "--profile",
                context["profile"],
                "--require-approval",
                "never",
                "--outputs-file",
                str(Path(context["repo_root"]) / context["cfn_outputs"]),
                "-c",
                f"deployment_config={context['deployment_config']}",
                "-c",
                f"image_tag={context['image_version_tag']}",
                "-c",
                f"schedule_test_expression={os.getenv('SCHEDULE_TEST_EXPRESSION', 'rate(10 minutes)')}",
            ],
            "cdk_deploy.log",
            "infra/cdk",
            False,
        ),
        (
            "config-render-from-outputs",
            _docker_cli_args(
                context,
                "config-render-from-outputs",
                "--stack",
                context["stack_name"],
                "--region",
                context["region"],
                "--profile",
                context["profile"],
                "--deployment",
                context["deployment_config"],
                "--output",
                context["runtime_config"],
                mount_generated=True,
                mount_runtime=False,
            ),
            "config_render_from_outputs.log",
            None,
            False,
        ),
        (
            "ecr-login",
            [
                f"aws ecr get-login-password --region {context['region']} --profile {context['profile']} "
                f"| docker login --username AWS --password-stdin {account_ref}.dkr.ecr.{context['region']}.amazonaws.com"
            ],
            "ecr_login.log",
            None,
            True,
        ),
        (
            "ecr-tag-versioned",
            ["docker", "tag", context["image_tag"], f"{ecr_repo}:{context['image_version_tag']}"],
            "ecr_tag_versioned.log",
            None,
            False,
        ),
        (
            "ecr-tag-latest",
            ["docker", "tag", context["image_tag"], f"{ecr_repo}:latest"],
            "ecr_tag_latest.log",
            None,
            False,
        ),
        (
            "ecr-push-versioned",
            ["docker", "push", f"{ecr_repo}:{context['image_version_tag']}"],
            "ecr_push_versioned.log",
            None,
            False,
        ),
        (
            "ecr-push-latest",
            ["docker", "push", f"{ecr_repo}:latest"],
            "ecr_push_latest.log",
            None,
            False,
        ),
    ]
    return _run_commands(context, "preflight", commands, dry_run=dry_run)


def validate_static(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    commands = [
        (
            "pytest-unit",
            [sys.executable, "-m", "pytest", "tests/unit", "-q"],
            "unit.log",
            None,
            False,
        ),
        (
            "pytest-contract",
            [sys.executable, "-m", "pytest", "tests/contract", "-q"],
            "contract.log",
            None,
            False,
        ),
        (
            "config-validate-all",
            [sys.executable, "-m", "eml_transformer.cli", "config-validate-all", "--directory", "configs/deployments"],
            "config_validate_all.log",
            None,
            False,
        ),
    ]
    return _run_commands(context, "static", commands, dry_run=dry_run)


def validate_container(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    commands: list[tuple[str, list[str], str, str | None, bool]] = [
        ("cli-help", _docker_cli_args(context, "--help", with_aws=False), "help.log", None, False),
        ("sources", _docker_cli_args(context, "sources", with_aws=False), "sources.log", None, False),
        (
            "deployment-matrix-help",
            _docker_cli_args(context, "deployment-matrix", "--help", with_aws=False),
            "deployment_matrix_help.log",
            None,
            False,
        ),
        (
            "config-validate-help",
            _docker_cli_args(context, "config-validate", "--help", with_aws=False),
            "config_validate_help.log",
            None,
            False,
        ),
    ]
    for deployment_path in sorted((Path(context["repo_root"]) / "configs" / "deployments").glob("*.yaml")):
        deployment_rel = repo_relative_path(deployment_path, Path(context["repo_root"]))
        name = deployment_path.stem
        commands.extend(
            [
                (
                    f"validate-{name}",
                    _docker_cli_args(context, "config-validate", "--deployment", deployment_rel, with_aws=False),
                    f"{name}_validate.log",
                    None,
                    False,
                ),
                (
                    f"info-{name}",
                    _docker_cli_args(context, "deployment-info", "--deployment", deployment_rel, with_aws=False),
                    f"{name}_info.log",
                    None,
                    False,
                ),
                (
                    f"matrix-{name}",
                    _docker_cli_args(context, "deployment-matrix", "--deployment", deployment_rel, with_aws=False),
                    f"{name}_matrix.log",
                    None,
                    False,
                ),
            ]
        )
    return _run_commands(context, "container", commands, dry_run=dry_run)


def validate_infra(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    commands = [
        (
            "verify-infra",
            _docker_cli_args(context, "verify-infra", "--config", context["runtime_config"]),
            "verify_infra.log",
            None,
            False,
        )
    ]
    return _run_commands(context, "infra", commands, dry_run=dry_run)


def validate_gdelt(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    max_files: int = 1,
    max_messages: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    gdelt_date = os.getenv("GDELT_DATE", "today")
    commands = [
        (
            "gdelt-discover-dry",
            _docker_cli_args(
                context,
                "gdelt-discover",
                "--date",
                gdelt_date,
                "--config",
                context["runtime_config"],
                "--max-files",
                str(max_files),
                "--no-enqueue",
            ),
            "gdelt_discover_dry.log",
            None,
            False,
        ),
        (
            "gdelt-discover-enqueue",
            _docker_cli_args(
                context,
                "gdelt-discover",
                "--date",
                gdelt_date,
                "--config",
                context["runtime_config"],
                "--max-files",
                str(max_files),
            ),
            "gdelt_discover_enqueue.log",
            None,
            False,
        ),
        (
            "verify-infra",
            _docker_cli_args(context, "verify-infra", "--config", context["runtime_config"]),
            "verify_infra.log",
            None,
            False,
        ),
        (
            "article-fetch-worker",
            _docker_cli_args(
                context,
                "article-fetch-worker",
                "--config",
                context["runtime_config"],
                "--max-messages",
                str(max_messages),
                "--output-format",
                "jsonl.gz",
                "--output-batch-size",
                str(max_messages),
                "--request-delay-seconds",
                "1",
            ),
            "article_fetch_worker.log",
            None,
            False,
        ),
    ]
    return _run_commands(context, "gdelt", commands, dry_run=dry_run)


def validate_pipeline(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    sources = ["iem_afos", "weather_alerts", "miso_notifications"]
    if os.getenv("NEWSAPI_KEY"):
        sources.append("newsapi")

    commands: list[tuple[str, list[str], str, str | None, bool]] = []
    for source in sources:
        commands.append(
            (
                f"standardize-{source}",
                _docker_cli_args(context, "standardize", "--source", source, "--config", context["runtime_config"]),
                f"standardize_{source}.log",
                None,
                False,
            )
        )
    for source in sources:
        commands.append(
            (
                f"embed-{source}",
                _docker_cli_args(context, "embed", "--source", source, "--config", context["runtime_config"]),
                f"embed_{source}.log",
                None,
                True,
            )
        )
    return _run_commands(context, "pipeline", commands, dry_run=dry_run)


def validate_batch(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    timeout: int = 300,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    gdelt_date = os.getenv("GDELT_DATE", "today")
    results = _run_commands(
        context,
        "batch",
        [
            (
                "submit-gdelt-discovery",
                _docker_cli_args(
                    context,
                    "aws-start-service",
                    "--service",
                    "gdelt_discovery",
                    "--config",
                    context["runtime_config"],
                    "--batch",
                    "--date",
                    gdelt_date,
                ),
                "submit_gdelt_discovery.log",
                None,
                False,
            ),
        ],
        dry_run=dry_run,
    )
    job_id = _extract_job_id(_last_output(results))
    if job_id or dry_run:
        wait_result = run_logged(
            "batch-wait-gdelt-discovery",
            _docker_cli_args(
                context,
                "batch-wait",
                "--job-id",
                job_id or "DRY_RUN_JOB_ID",
                "--region",
                context["region"],
                "--timeout",
                str(timeout),
                "--poll-interval",
                "30",
            ),
            context=context,
            log_name="wait_gdelt_discovery.log",
            action="batch",
            dry_run=dry_run,
        )
        results["commands"].append(wait_result)
    return _finish(results)


def validate_e2e(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    commands = [
        (
            "start-backfill",
            _docker_cli_args(
                context,
                "aws-start-service",
                "--service",
                "backfill",
                "--config",
                context["runtime_config"],
                "--source",
                "all",
                "--start-date",
                os.getenv("BACKFILL_START_DATE", "2026-06-06"),
                "--end-date",
                os.getenv("BACKFILL_END_DATE", "2026-06-12"),
                "--window-days",
                os.getenv("BACKFILL_WINDOW_DAYS", "7"),
                "--init-checkpoint",
                "--state-machine",
            ),
            "start_backfill.log",
            None,
            False,
        ),
        (
            "verify-infra",
            _docker_cli_args(context, "verify-infra", "--config", context["runtime_config"]),
            "verify_infra.log",
            None,
            True,
        ),
    ]
    return _run_commands(context, "e2e", commands, dry_run=dry_run)


def cleanup_test_resources(
    deployment: str | Path,
    *,
    profile: str | None = None,
    results_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    context = build_validation_context(deployment, profile=profile, results_dir=results_dir)
    _require_runtime_config(context, dry_run=dry_run)
    commands = [
        (
            "cleanup-test-resources",
            _docker_cli_args(
                context,
                "cleanup-test-resources",
                "--config",
                context["runtime_config"],
                "--dry-run" if dry_run else "",
            ),
            "cleanup_test_resources.log",
            None,
            True,
        )
    ]
    commands = [(name, [arg for arg in argv if arg], log, cwd, allow) for name, argv, log, cwd, allow in commands]
    return _run_commands(context, "cleanup", commands, dry_run=dry_run)


def reset_stack(
    deployment: str | Path,
    *,
    confirm_stack: str,
    profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return reset_stack_from_deployment(
        deployment,
        confirm_stack=confirm_stack,
        profile=profile,
        dry_run=dry_run,
    )


def _run_commands(
    context: dict[str, Any],
    action: str,
    commands: list[tuple[str, list[str], str, str | None, bool]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    results = {
        "action": action,
        "context": context_payload(context),
        "commands": [],
        "ok": True,
    }

    for name, argv, log_name, cwd, allow_failure in commands:
        result = run_logged(
            name,
            argv,
            context=context,
            action=action,
            log_name=log_name,
            cwd=cwd,
            allow_failure=allow_failure,
            shell=len(argv) == 1 and "|" in argv[0],
            dry_run=dry_run,
        )
        results["commands"].append(result)
        if not result["ok"]:
            results["ok"] = False
            break

    return _finish(results)


def _finish(results: dict[str, Any]) -> dict[str, Any]:
    results["ok"] = all(command.get("ok", False) for command in results["commands"])
    return results


def _docker_cli_args(
    context: dict[str, Any],
    *args: str,
    with_aws: bool = True,
    mount_generated: bool = False,
    mount_runtime: bool = True,
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    repo_root = Path(context["repo_root"])
    argv = ["docker", "run", "--rm"]

    if with_aws:
        argv.extend(
            [
                "-v",
                f"{Path.home() / '.aws'}:/root/.aws",
                "-e",
                f"AWS_PROFILE={context['profile']}",
                "-e",
                "AWS_SDK_LOAD_CONFIG=1",
                "-e",
                f"AWS_REGION={context['region']}",
            ]
        )

    argv.extend(["-v", f"{repo_root / 'configs'}:/app/configs:ro"])

    runtime_config = repo_root / context["runtime_config"]
    if with_aws and mount_runtime:
        argv.extend(["-v", f"{runtime_config}:/app/{context['runtime_config']}:ro"])
    if mount_generated:
        argv.extend(["-v", f"{repo_root / 'configs' / 'generated'}:/app/configs/generated"])

    for key, value in (extra_env or {}).items():
        argv.extend(["-e", f"{key}={value}"])

    argv.extend([context["image_tag"], *args])
    return argv


def _base_env(context: dict[str, Any]) -> dict[str, str]:
    base = {
        "AWS_PROFILE": context["profile"],
        "AWS_REGION": context["region"],
        "DEPLOYMENT_CONFIG": context["deployment_config"],
        "IMAGE_TAG": context["image_tag"],
        "IMAGE_VERSION_TAG": context["image_version_tag"],
        "CDK_TEST_IMAGE_TAG": context["cdk_test_image_tag"],
        "BUILD_EXTRAS": context["build_extras"],
    }
    if os.getenv("AWS_ACCOUNT_ID"):
        base["AWS_ACCOUNT_ID"] = os.getenv("AWS_ACCOUNT_ID", "")
    return base


def _expand_env_refs(value: str, env: dict[str, str]) -> str:
    expanded = value
    for key, env_value in env.items():
        expanded = expanded.replace("${" + key + "}", env_value)
    return expanded


def _prepare_preflight_environment(context: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    account_id = _check_sso_token(context)
    os.environ["AWS_ACCOUNT_ID"] = account_id
    _resolve_runtime_secret_arns(context)
    _resolve_batch_network(context)
    _ensure_cdk_venv(context)


def _check_sso_token(context: dict[str, Any]) -> str:
    try:
        output = subprocess.check_output(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                context["profile"],
                "--query",
                "Account",
                "--output",
                "text",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        subprocess.check_call(["aws", "sso", "login", "--profile", context["profile"]])
        output = subprocess.check_output(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                context["profile"],
                "--query",
                "Account",
                "--output",
                "text",
            ],
            text=True,
        )
    return output.strip()


def _resolve_runtime_secret_arns(context: dict[str, Any]) -> None:
    secret_cfg = context["config"].get("runtime_secrets", {}).get("NEWSAPI_KEY", {})
    env_key = secret_cfg.get("secret_arn_env") if isinstance(secret_cfg, dict) else None
    if not env_key or os.getenv(env_key):
        return

    secret_name = os.getenv("NEWSAPI_SECRET_NAME")
    if not secret_name:
        raise RuntimeError(
            "NEWSAPI_SECRET_NAME is required because the deployment declares "
            "runtime_secrets.NEWSAPI_KEY."
        )

    arn = subprocess.check_output(
        [
            "aws",
            "secretsmanager",
            "describe-secret",
            "--secret-id",
            secret_name,
            "--region",
            context["region"],
            "--profile",
            context["profile"],
            "--query",
            "ARN",
            "--output",
            "text",
        ],
        text=True,
    ).strip()
    os.environ[env_key] = arn


def _resolve_batch_network(context: dict[str, Any]) -> None:
    network = context["config"].get("network", {})
    subnet_ids = _csv_without_placeholders(network.get("subnet_ids", []))
    security_group_ids = _csv_without_placeholders(network.get("security_group_ids", []))

    if subnet_ids and not os.getenv("BATCH_SUBNET_IDS"):
        os.environ["BATCH_SUBNET_IDS"] = subnet_ids
    if security_group_ids and not os.getenv("BATCH_SECURITY_GROUP_IDS"):
        os.environ["BATCH_SECURITY_GROUP_IDS"] = security_group_ids
    if os.getenv("BATCH_SUBNET_IDS") and os.getenv("BATCH_SECURITY_GROUP_IDS"):
        return

    vpc_id = subprocess.check_output(
        [
            "aws",
            "ec2",
            "describe-vpcs",
            "--filters",
            "Name=is-default,Values=true",
            "--region",
            context["region"],
            "--profile",
            context["profile"],
            "--query",
            "Vpcs[0].VpcId",
            "--output",
            "text",
        ],
        text=True,
    ).strip()
    if not vpc_id or vpc_id == "None":
        raise RuntimeError(
            f"No default VPC found in {context['region']}; set BATCH_SUBNET_IDS "
            "and BATCH_SECURITY_GROUP_IDS before running preflight."
        )

    if not os.getenv("BATCH_SUBNET_IDS"):
        os.environ["BATCH_SUBNET_IDS"] = subprocess.check_output(
            [
                "aws",
                "ec2",
                "describe-subnets",
                "--filters",
                f"Name=vpc-id,Values={vpc_id}",
                "--region",
                context["region"],
                "--profile",
                context["profile"],
                "--query",
                "Subnets[].SubnetId",
                "--output",
                "text",
            ],
            text=True,
        ).strip().replace("\t", ",")

    if not os.getenv("BATCH_SECURITY_GROUP_IDS"):
        os.environ["BATCH_SECURITY_GROUP_IDS"] = subprocess.check_output(
            [
                "aws",
                "ec2",
                "describe-security-groups",
                "--filters",
                f"Name=vpc-id,Values={vpc_id}",
                "Name=group-name,Values=default",
                "--region",
                context["region"],
                "--profile",
                context["profile"],
                "--query",
                "SecurityGroups[0].GroupId",
                "--output",
                "text",
            ],
            text=True,
        ).strip()


def _ensure_cdk_venv(context: dict[str, Any]) -> None:
    venv = Path(context["repo_root"]) / ".venv-cdk"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        return
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])


def _cdk_python(context: dict[str, Any]) -> Path:
    configured = os.getenv("CDK_PYTHON")
    if configured:
        return Path(configured)

    venv = Path(context["repo_root"]) / ".venv-cdk"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _require_runtime_config(context: dict[str, Any], *, dry_run: bool) -> None:
    runtime_config = Path(context["repo_root"]) / context["runtime_config"]
    if not dry_run and not runtime_config.exists():
        raise FileNotFoundError(
            f"Runtime config not found: {runtime_config}. Run aws-preflight or config-render first."
        )


def _csv_without_placeholders(values: object) -> str:
    if not isinstance(values, list):
        return ""
    clean = [str(value) for value in values if "replace-me" not in str(value)]
    return ",".join(clean)


def _extract_job_id(output: str) -> str | None:
    for line in output.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        job_id = _find_job_id(payload)
        if job_id:
            return job_id
    marker = '"job_id"'
    if marker in output:
        try:
            start = output.index(marker)
            tail = output[start:].split('"', 4)
            return tail[3]
        except (ValueError, IndexError):
            return None
    return None


def _find_job_id(value: Any) -> str | None:
    if isinstance(value, dict):
        job_id = value.get("job_id") or value.get("jobId")
        if job_id:
            return str(job_id)
        for child in value.values():
            found = _find_job_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_job_id(child)
            if found:
                return found
    return None


def _last_output(results: dict[str, Any]) -> str:
    if not results.get("commands"):
        return ""
    return str(results["commands"][-1].get("output", ""))
