from __future__ import annotations

from typing import Any, Callable

import typer

from eml_transformer.deployment.aws_validation import (
    aws_preflight,
    cleanup_test_resources,
    reset_stack,
    validate_batch,
    validate_container,
    validate_e2e,
    validate_gdelt,
    validate_infra,
    validate_pipeline,
    validate_static,
)


JsonPrinter = Callable[[str, dict[str, Any]], None]


def register(app: typer.Typer, print_json_result: JsonPrinter) -> None:
    @app.command("aws-preflight")
    def aws_preflight_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = aws_preflight(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Preflight", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-static")
    def aws_validate_static_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_static(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Static Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-container")
    def aws_validate_container_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_container(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Container Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-infra")
    def aws_validate_infra_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_infra(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Infra Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-gdelt")
    def aws_validate_gdelt_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        max_files: int = typer.Option(1, "--max-files"),
        max_messages: int = typer.Option(5, "--max-messages"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_gdelt(
            deployment,
            profile=profile,
            max_files=max_files,
            max_messages=max_messages,
            dry_run=dry_run,
        )
        _print("AWS GDELT Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-pipeline")
    def aws_validate_pipeline_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_pipeline(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Pipeline Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-batch")
    def aws_validate_batch_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        timeout: int = typer.Option(300, "--timeout"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_batch(
            deployment,
            profile=profile,
            timeout=timeout,
            dry_run=dry_run,
        )
        _print("AWS Batch Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-validate-e2e")
    def aws_validate_e2e_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = validate_e2e(deployment, profile=profile, dry_run=dry_run)
        _print("AWS E2E Validation", result, json_output, print_json_result)
        _exit_on_failure(result)

    @app.command("aws-reset-stack")
    def aws_reset_stack_command(
        deployment: str = typer.Option(..., "--deployment"),
        confirm_stack: str = typer.Option(..., "--confirm-stack"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = reset_stack(
            deployment,
            confirm_stack=confirm_stack,
            profile=profile,
            dry_run=dry_run,
        )
        _print("AWS Stack Reset", result, json_output, print_json_result)

    @app.command("aws-cleanup-test-resources")
    def aws_cleanup_test_resources_command(
        deployment: str = typer.Option(..., "--deployment"),
        profile: str | None = typer.Option(None, "--profile"),
        json_output: bool = typer.Option(False, "--json"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        result = cleanup_test_resources(deployment, profile=profile, dry_run=dry_run)
        _print("AWS Cleanup Test Resources", result, json_output, print_json_result)
        _exit_on_failure(result)


def _print(
    title: str,
    result: dict[str, Any],
    json_output: bool,
    print_json_result: JsonPrinter,
) -> None:
    if json_output:
        import json

        typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))
        return
    print_json_result(title, result)


def _exit_on_failure(result: dict[str, Any]) -> None:
    if result.get("ok") is False:
        raise typer.Exit(code=1)
