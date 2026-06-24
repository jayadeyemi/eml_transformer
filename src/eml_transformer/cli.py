from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from dotenv import load_dotenv

from eml_transformer.cloud.aws.config import load_aws_runtime_config
from eml_transformer.cloud.aws.runtime import AwsAcquisitionRuntime
from eml_transformer.deployment.config import (
    assert_valid_deployment_config,
    deployment_metadata as build_deployment_metadata,
    deployment_config_warnings,
    deployment_matrix as build_deployment_matrix,
    load_deployment_config,
    render_runtime_config,
    render_runtime_config_from_cfn_outputs,
    write_yaml,
)
from eml_transformer.ingestion.registry import available_sources
from eml_transformer.logging import setup_logging
from eml_transformer.pipelines.backfill_pipeline import BackfillPipeline
from eml_transformer.pipelines.ingestion_pipeline import IngestionPipeline
from eml_transformer.pipelines.standardization_pipeline import StandardizationPipeline
from eml_transformer.pipelines.scraping_pipeline import ScrapingPipeline
from eml_transformer.runtime import build_runtime
from eml_transformer.services.collection import CollectionServiceRunner
from eml_transformer.utils.config import DEFAULT_RUNTIME_CONFIG, apply_environment_overrides

load_dotenv()

app = typer.Typer()
logger = logging.getLogger(__name__)


def _result_rows(results: Any) -> list[dict[str, Any]]:
    if results is None:
        return []

    if isinstance(results, dict):
        if not any(
            isinstance(value, (dict, list, tuple)) or hasattr(value, "to_summary")
            for value in results.values()
        ):
            return [results]

        rows: list[dict[str, Any]] = []
        for value in results.values():
            rows.extend(_result_rows(value))
        return rows

    if isinstance(results, (list, tuple)):
        rows = []
        for value in results:
            rows.extend(_result_rows(value))
        return rows

    if hasattr(results, "to_summary"):
        return [results.to_summary()]

    return [{"result": str(results)}]


def print_result_table(title: str, results: Any) -> None:
    rows = _result_rows(results)

    if not rows:
        typer.echo(f"\n{title}: no results")
        return

    df = pd.DataFrame(rows)

    typer.echo("\n" + "=" * 100)
    typer.echo(title.upper())
    typer.echo("=" * 100)
    typer.echo(df.to_string(index=False, max_colwidth=40))
    typer.echo("=" * 100 + '\n')


def print_json_result(title: str, result: dict[str, Any]) -> None:
    typer.echo("\n" + "=" * 100)
    typer.echo(title.upper())
    typer.echo("=" * 100)
    typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))
    typer.echo("=" * 100 + "\n")


def _has_failed_result(value: Any) -> bool:
    if value is None:
        return False

    if hasattr(value, "to_summary"):
        return _has_failed_result(value.to_summary())

    if isinstance(value, dict):
        if str(value.get("status", "")).lower() == "failed":
            return True

        return any(_has_failed_result(child) for child in value.values())

    if isinstance(value, (list, tuple)):
        return any(_has_failed_result(child) for child in value)

    return False


def build_aws_runtime(
    config: str,
    aws_profile: str | None = None,
) -> AwsAcquisitionRuntime:
    """Build an AwsAcquisitionRuntime from a runtime config YAML or env vars.

    When *config* file exists it is loaded normally (env vars still override
    YAML values via ``apply_environment_overrides``).  When the file is absent
    — e.g. inside an AWS Batch container where CDK injects all runtime values
    as environment variables — the function falls back to a pure env-vars
    construction so no config file needs to be baked into the image.

    ``aws_profile`` overrides any profile set in the config file or
    ``AWS_PROFILE`` environment variable.
    """
    import os
    from pathlib import Path as _Path

    if aws_profile:
        # boto3 respects AWS_PROFILE; set it so the Session picks it up even
        # if AwsRuntimeConfig was constructed before the flag was parsed.
        os.environ.setdefault("AWS_PROFILE", aws_profile)

    if _Path(config).exists():
        rt = build_runtime(config)
        cfg = dict(rt.cfg)
        storage = rt.storage
    else:
        # Env-vars-only mode: CDK injects DATA_BUCKET, URL_FETCH_QUEUE_URL, etc.
        from eml_transformer.storage.storage import make_storage
        cfg = {}
        apply_environment_overrides(cfg)
        storage = make_storage(cfg.get("storage", {"backend": "s3"}))

    # Inject profile into the aws sub-dict so load_aws_runtime_config picks it up.
    aws_sub = dict(cfg.get("aws", {}))
    if aws_profile and not aws_sub.get("profile"):
        aws_sub["profile"] = aws_profile
    cfg["aws"] = aws_sub
    aws_config = load_aws_runtime_config(cfg)
    return AwsAcquisitionRuntime(
        config=aws_config,
        storage=storage,
        service_config_path=config,
    )


def get_source_config(
    source: str,
    source_configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if source not in source_configs:
        available = ", ".join(sorted(source_configs))
        raise typer.BadParameter(
            f"Unknown source: {source}. Available sources: {available}"
        )

    return source_configs[source]


@app.callback()
def main(
    log_level: str = typer.Option("INFO"),
):
    setup_logging(
        level=getattr(logging, log_level.upper()),
        log_file=None,
        force=False,
    )


@app.command()
def sources():
    typer.echo("Available sources:")

    for source in available_sources():
        typer.echo(f"- {source}")


@app.command("config-validate")
def config_validate(
    deployment: str = typer.Option(..., "--deployment"),
):
    loaded = load_deployment_config(deployment)
    assert_valid_deployment_config(loaded.config)
    print_json_result(
        "Deployment Config Validation",
        {
            "deployment": str(loaded.path),
            "valid": True,
            "warnings": deployment_config_warnings(loaded.config),
            "layers": [str(layer) for layer in loaded.layers],
        },
    )


@app.command("config-validate-all")
def config_validate_all(
    directory: str = typer.Option("configs/deployments", "--directory"),
):
    deployment_dir = Path(directory)
    results = []
    failed = []

    for path in sorted(deployment_dir.glob("*.yaml")):
        loaded = load_deployment_config(path)

        try:
            assert_valid_deployment_config(loaded.config)
            valid = True
            errors = []
        except ValueError as exc:
            valid = False
            errors = str(exc).splitlines()
            failed.append(str(path))

        results.append(
            {
                "deployment": str(path),
                "valid": valid,
                "errors": errors,
                "warnings": deployment_config_warnings(loaded.config),
            }
        )

    print_json_result(
        "Deployment Config Validation",
        {
            "directory": str(deployment_dir.resolve()),
            "results": results,
        },
    )

    if failed:
        raise typer.Exit(code=1)


@app.command("config-render")
def config_render(
    deployment: str = typer.Option(..., "--deployment"),
    output: str = typer.Option(..., "--output"),
):
    loaded = load_deployment_config(deployment)
    assert_valid_deployment_config(loaded.config)
    rendered = render_runtime_config(loaded.config)
    write_yaml(Path(output), rendered)
    print_json_result(
        "Rendered Runtime Config",
        {
            "deployment": str(loaded.path),
            "output": str(Path(output).resolve()),
            "warnings": deployment_config_warnings(rendered),
            "layers": [str(layer) for layer in loaded.layers],
        },
    )


@app.command("config-render-from-outputs")
def config_render_from_outputs(
    stack: str = typer.Option(
        ...,
        "--stack",
        help="CloudFormation stack name to read outputs from.",
    ),
    region: str = typer.Option("us-east-1", "--region"),
    output: str = typer.Option(..., "--output"),
    profile: str | None = typer.Option(
        None, "--profile", help="AWS profile name for CloudFormation API calls."
    ),
    deployment: str | None = typer.Option(
        None,
        "--deployment",
        help="Optional deployment YAML whose source/path/model settings are merged into the runtime file.",
    ),
):
    """Produce a runtime config YAML from an already-deployed CloudFormation stack."""
    import boto3 as _boto3

    cfn_client = _boto3.Session(
        profile_name=profile or None,
        region_name=region,
    ).client("cloudformation")
    rendered = render_runtime_config_from_cfn_outputs(
        stack_name=stack, region=region, _cfn_client=cfn_client
    )
    layers: list[str] = []
    if deployment:
        loaded = load_deployment_config(deployment)
        assert_valid_deployment_config(loaded.config)
        rendered["paths"] = loaded.config.get("paths", {"root": "."})
        rendered["sources"] = loaded.config.get("sources", {})
        rendered["embeddings"] = loaded.config.get("embeddings", {})
        layers = [str(layer) for layer in loaded.layers]
    write_yaml(Path(output), rendered)
    print_json_result(
        "Rendered Runtime Config From CDK Outputs",
        {
            "stack": stack,
            "region": region,
            "output": str(Path(output).resolve()),
            "deployment": deployment,
            "layers": layers,
        },
    )


@app.command("deployment-matrix")
def deployment_matrix(
    deployment: str = typer.Option(..., "--deployment"),
):
    loaded = load_deployment_config(deployment)
    assert_valid_deployment_config(loaded.config)
    print_json_result("Deployment Matrix", build_deployment_matrix(loaded.config))


@app.command("deployment-info")
def deployment_info(
    deployment: str = typer.Option(..., "--deployment"),
):
    loaded = load_deployment_config(deployment)
    assert_valid_deployment_config(loaded.config)
    print_json_result("Deployment Info", build_deployment_metadata(loaded.config))


@app.command()
def ingest(
    source: str = typer.Option("all"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG),
):
    rt = build_runtime(config)

    pipeline = IngestionPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    if source.lower() == "all":
        results = pipeline.run_all(rt.source_configs)
    else:
        source_config = get_source_config(source, rt.source_configs)
        results = [pipeline.run_source(source, source_config)]

    print_result_table("Ingestion Results", results)


@app.command("standardize")
def standardize(
    source: str = typer.Option("all"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG),
):
    rt = build_runtime(config)

    pipeline = StandardizationPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    if source.lower() == "all":
        results = pipeline.run_all(rt.source_configs)
    else:
        source_config = get_source_config(source, rt.source_configs)
        results = [pipeline.run_source(source, source_config)]

    print_result_table("Standardization Results", results)

@app.command("scrape")
def scrape(
    source: str = typer.Option("all"),
    config: str = typer.Option("configs/dev.yaml"),
):
    rt = build_runtime(config)

    pipeline = ScrapingPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    if source.lower() == "all":
        results = pipeline.run_all(rt.source_configs)
    else:
        source_config = get_source_config(source, rt.source_configs)
        results = [pipeline.run_source(source, source_config)]

    print_result_table("Scraping Results", results)

@app.command()
def embed(
    source: str = typer.Option("all"),
    model_name: str | None = typer.Option(None, "--model", "-m"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG),
):
    from eml_transformer.pipelines.embedding_pipeline import EmbeddingPipeline

    rt = build_runtime(config)

    embedding_config = dict(rt.embedding_config)

    if model_name is not None:
        embedding_config["model"] = model_name

    pipeline = EmbeddingPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    if source.lower() == "all":
        results = pipeline.run_all(
            embedding_config=embedding_config,
            source_configs=rt.embedding_source_configs,
        )
    else:
<<<<<<< HEAD
        source_config = get_source_config(source, rt.embedding_source_configs)
=======
        source_config = get_source_config(source, rt.source_configs)
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456

        results = [
            pipeline.run_source(
                source=source,
                embedding_config=embedding_config,
<<<<<<< HEAD
                source_config=source_config,
=======
                source_config=source_config
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456
            )
        ]

    print_result_table("Embedding Results", results)


@app.command("run-all")
def run_all(
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG),
):
    from eml_transformer.pipelines.embedding_pipeline import EmbeddingPipeline

    rt = build_runtime(config)

    ingestion_results = IngestionPipeline(
        storage=rt.storage,
        paths=rt.paths,
    ).run_all(rt.source_configs)

    print_result_table("Ingestion Results", ingestion_results)

    standardization_results = StandardizationPipeline(
        storage=rt.storage,
        paths=rt.paths,
    ).run_all(rt.source_configs)

    print_result_table("Standardization Results", standardization_results)

    embedding_results = EmbeddingPipeline(
        storage=rt.storage,
        paths=rt.paths,
    ).run_all(
        embedding_config=rt.embedding_config,
        source_configs=rt.embedding_source_configs,
    )

    print_result_table("Embedding Results", embedding_results)


@app.command()
def backfill(
    source: str = typer.Option(..., "--source", "-s"),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    window_days: int = typer.Option(30, "--window-days"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    init_checkpoint: bool = typer.Option(False, "--init-checkpoint"),
):
    rt = build_runtime(config)

    ingestion_pipeline = IngestionPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    pipeline = BackfillPipeline(
        ingestion_pipeline=ingestion_pipeline,
    )

    if source.lower() == "all":
        results = pipeline.run_all(
            source_configs=rt.source_configs,
            start_date=start_date,
            end_date=end_date,
            window_days=window_days,
            seed_checkpoint=init_checkpoint,
        )
    else:
        source_config = get_source_config(source, rt.source_configs)
    
        results = [
            pipeline.run_source(
                source_name=source,
                source_config=source_config,
                start_date=start_date,
                end_date=end_date,
                window_days=window_days,
                seed_checkpoint=init_checkpoint,
            )
        ]

    print_result_table("Backfill Results", results)


@app.command("service-run")
def service_run(
    service: str = typer.Option(..., "--service"),
    source: str = typer.Option("all", "--source", "-s"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    window_days: int = typer.Option(30, "--window-days"),
    model_name: str | None = typer.Option(None, "--model", "-m"),
    init_checkpoint: bool = typer.Option(False, "--init-checkpoint"),
):
    runner = CollectionServiceRunner(config_path=config)
    result = runner.run(
        service=service,
        source=source,
        start_date=start_date,
        end_date=end_date,
        window_days=window_days,
        model_name=model_name,
        init_checkpoint=init_checkpoint,
    )
    print_json_result("Collection Service", result)

    if _has_failed_result(result):
        raise typer.Exit(code=1)


@app.command("storage-aggregate-transfer")
def storage_aggregate_transfer(
    source_prefix: str = typer.Option(
        ...,
        "--source-prefix",
        help="Storage prefix to aggregate, for example bronze/articles/batches/.",
    ),
    name: str = typer.Option(
        ...,
        "--name",
        help="Human-readable aggregate name used in output paths.",
    ),
    input_format: str = typer.Option(
        "json",
        "--input-format",
        help="json for .json/.jsonl/.jsonl.gz files, or parquet for parquet files.",
    ),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    run_id: str | None = typer.Option(None, "--run-id"),
    output_prefix: str = typer.Option(
        "transfer/aggregates",
        "--output-prefix",
    ),
    manifest_prefix: str = typer.Option(
        "manifests/transfer_aggregates",
        "--manifest-prefix",
    ),
    target_rows: int = typer.Option(
        10_000,
        "--target-rows",
        help="Approximate maximum rows per aggregate part.",
    ),
    max_files: int | None = typer.Option(
        None,
        "--max-files",
        help="Limit source files for pilot runs.",
    ),
    envelope: bool = typer.Option(
        True,
        "--envelope/--flat",
        help="Wrap JSON records with source-key provenance instead of adding flat metadata fields.",
    ),
):
    from eml_transformer.storage.transfer import TransferAggregator

    rt = build_runtime(config)
    aggregator = TransferAggregator(rt.storage)
    normalized_format = input_format.strip().lower()

    if normalized_format in {"json", "jsonl", "jsonl.gz", "auto"}:
        result = aggregator.aggregate_json_records(
            source_prefix=source_prefix,
            name=name,
            run_id=run_id,
            output_prefix=output_prefix,
            manifest_prefix=manifest_prefix,
            target_rows=target_rows,
            envelope=envelope,
            max_files=max_files,
        )
    elif normalized_format == "parquet":
        result = aggregator.aggregate_parquet_records(
            source_prefix=source_prefix,
            name=name,
            run_id=run_id,
            output_prefix=output_prefix,
            manifest_prefix=manifest_prefix,
            target_rows=target_rows,
            max_files=max_files,
        )
    else:
        raise typer.BadParameter(
            "input_format must be one of: json, jsonl, jsonl.gz, auto, parquet"
        )

    print_json_result("Storage Transfer Aggregate", result.to_summary())


@app.command("gdelt-discover")
def gdelt_discover(
    date: str = typer.Option(..., "--date", help="GDELT UTC date as YYYY-MM-DD."),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    run_id: str | None = typer.Option(None, "--run-id"),
    max_files: int | None = typer.Option(
        None,
        "--max-files",
        help="Limit 15-minute GDELT files for pilot runs.",
    ),
    max_urls: int | None = typer.Option(
        None,
        "--max-urls",
        help="Limit queued GDELT candidate URLs for pilot runs.",
    ),
    enqueue: bool = typer.Option(
        True,
        "--enqueue/--no-enqueue",
        help="Send discovered URLs to the configured SQS queue.",
    ),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    result = runtime.discover_and_enqueue(
        date=date,
        run_id=run_id,
        max_files=max_files,
        max_urls=max_urls,
        enqueue=enqueue,
    )
    print_json_result("GDELT Discovery", result)


@app.command("gdelt-enqueue-urls")
def gdelt_enqueue_urls(
    key: str = typer.Option(
        ...,
        "--key",
        help="Storage key containing candidate URL JSON from gdelt-discover.",
    ),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    queued = runtime.enqueue_from_key(key)
    print_json_result("GDELT URL Enqueue", {"key": key, "urls_queued": queued})


@app.command("article-fetch-worker")
def article_fetch_worker(
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    run_id: str | None = typer.Option(None, "--run-id"),
    max_messages: int = typer.Option(10, "--max-messages"),
    wait_time_seconds: int = typer.Option(10, "--wait-time-seconds"),
    visibility_timeout: int = typer.Option(120, "--visibility-timeout"),
    request_delay_seconds: float = typer.Option(0.0, "--request-delay-seconds"),
    output_batch_size: int = typer.Option(1, "--output-batch-size"),
    output_format: str = typer.Option("json", "--output-format"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    result = runtime.fetch_articles(
        run_id=run_id,
        max_messages=max_messages,
        wait_time_seconds=wait_time_seconds,
        visibility_timeout=visibility_timeout,
        request_delay_seconds=request_delay_seconds,
        output_batch_size=output_batch_size,
        output_format=output_format,
    )
    print_json_result("Article Fetch Worker", result.__dict__)


@app.command("aws-start-service")
def aws_start_service(
    service: str = typer.Option(..., "--service"),
    source: str = typer.Option("all", "--source", "-s"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    run_id: str | None = typer.Option(None, "--run-id"),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    date: str | None = typer.Option(None, "--date"),
    max_files: int | None = typer.Option(None, "--max-files"),
    max_urls: int | None = typer.Option(None, "--max-urls"),
    max_messages: int | None = typer.Option(None, "--max-messages"),
    output_batch_size: int | None = typer.Option(None, "--output-batch-size"),
    output_format: str | None = typer.Option(None, "--output-format"),
    window_days: int | None = typer.Option(None, "--window-days"),
    model_name: str | None = typer.Option(None, "--model", "-m"),
    init_checkpoint: bool = typer.Option(False, "--init-checkpoint"),
    use_state_machine: bool = typer.Option(False, "--state-machine/--batch"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    parameters = {
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "date": date,
        "max_files": max_files,
        "max_urls": max_urls,
        "max_messages": max_messages,
        "output_batch_size": output_batch_size,
        "output_format": output_format,
        "window_days": window_days,
        "model_name": model_name,
        "init_checkpoint": init_checkpoint,
    }
    result = runtime.start_service(
        service=service,
        parameters=parameters,
        run_id=run_id,
        use_state_machine=use_state_machine,
    )
    print_json_result("AWS Collection Service Start", result)


@app.command("aws-restore-s3-object")
def aws_restore_s3_object(
    key: str = typer.Option(..., "--key"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    bucket: str | None = typer.Option(None, "--bucket"),
    version_id: str | None = typer.Option(None, "--version-id"),
    days: int = typer.Option(7, "--days"),
    tier: str = typer.Option("Bulk", "--tier"),
    run_id: str | None = typer.Option(None, "--run-id"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    result = runtime.restore_s3_object(
        key=key,
        bucket=bucket,
        version_id=version_id,
        days=days,
        tier=tier,
        run_id=run_id,
    )
    print_json_result("AWS S3 Restore Object", result)


@app.command("aws-s3-restore-status")
def aws_s3_restore_status(
    key: str = typer.Option(..., "--key"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    bucket: str | None = typer.Option(None, "--bucket"),
    version_id: str | None = typer.Option(None, "--version-id"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    result = runtime.s3_object_restore_status(
        key=key,
        bucket=bucket,
        version_id=version_id,
    )
    print_json_result("AWS S3 Restore Status", result)


@app.command("aws-rehydrate-s3-object")
def aws_rehydrate_s3_object(
    key: str = typer.Option(..., "--key"),
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    destination_key: str | None = typer.Option(None, "--destination-key"),
    bucket: str | None = typer.Option(None, "--bucket"),
    version_id: str | None = typer.Option(None, "--version-id"),
    storage_class: str = typer.Option("STANDARD", "--storage-class"),
    run_id: str | None = typer.Option(None, "--run-id"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    runtime = build_aws_runtime(config, aws_profile=profile)
    result = runtime.rehydrate_s3_object(
        key=key,
        destination_key=destination_key,
        bucket=bucket,
        version_id=version_id,
        storage_class=storage_class,
        run_id=run_id,
    )
    print_json_result("AWS S3 Rehydrate Object", result)


@app.command("verify-infra")
def verify_infra(
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
):
    """Verify all AWS infrastructure resources are accessible.

    Exits with code 0 when all checks pass, 1 when any check fails.
    Safe to run inside Docker containers: uses env-var-injected credentials.
    """
    import boto3 as _boto3

    runtime = build_aws_runtime(config, aws_profile=profile)
    cfg = runtime.config
    results: dict[str, dict] = {}

    session = _boto3.Session(
        profile_name=cfg.aws_profile or None,
        region_name=cfg.region,
    )

    # S3
    if cfg.infra_stack:
        bucket = runtime.storage.bucket if hasattr(runtime.storage, "bucket") else None
        if not bucket:
            import os
            bucket = os.getenv("DATA_BUCKET")
        if bucket:
            try:
                session.client("s3").head_bucket(Bucket=bucket)
                results["s3"] = {"status": "ok", "bucket": bucket}
            except Exception as exc:
                results["s3"] = {"status": "error", "bucket": bucket, "error": str(exc)}
        else:
            results["s3"] = {"status": "skipped", "reason": "DATA_BUCKET not configured"}

    # SQS
    if cfg.url_fetch_queue_url:
        try:
            session.client("sqs").get_queue_attributes(
                QueueUrl=cfg.url_fetch_queue_url,
                AttributeNames=["ApproximateNumberOfMessages"],
            )
            results["sqs"] = {"status": "ok", "queue_url": cfg.url_fetch_queue_url}
        except Exception as exc:
            results["sqs"] = {"status": "error", "queue_url": cfg.url_fetch_queue_url, "error": str(exc)}
    else:
        results["sqs"] = {"status": "skipped", "reason": "URL_FETCH_QUEUE_URL not configured"}

    # SNS notifications
    if cfg.sns_topic_arn:
        try:
            session.client("sns").get_topic_attributes(
                TopicArn=cfg.sns_topic_arn,
            )
            results["sns"] = {"status": "ok", "topic_arn": cfg.sns_topic_arn}
        except Exception as exc:
            results["sns"] = {
                "status": "error",
                "topic_arn": cfg.sns_topic_arn,
                "error": str(exc),
            }
    else:
        results["sns"] = {"status": "skipped", "reason": "SNS_TOPIC_ARN not configured"}

    # DynamoDB
    dynamo = session.client("dynamodb")
    for attr, label in [
        (cfg.url_state_table, "url_state"),
        (cfg.run_state_table, "run_state"),
        (cfg.domain_throttle_table, "domain_throttle"),
    ]:
        if attr:
            try:
                resp = dynamo.describe_table(TableName=attr)
                status = resp["Table"]["TableStatus"]
                results[f"dynamodb_{label}"] = {"status": "ok" if status == "ACTIVE" else "error", "table": attr, "table_status": status}
            except Exception as exc:
                results[f"dynamodb_{label}"] = {"status": "error", "table": attr, "error": str(exc)}
        else:
            results[f"dynamodb_{label}"] = {"status": "skipped", "reason": f"{label.upper()}_TABLE not configured"}

    # Batch job queue
    if cfg.batch_job_queue:
        try:
            resp = session.client("batch").describe_job_queues(jobQueues=[cfg.batch_job_queue])
            queues = resp.get("jobQueues", [])
            q_state = queues[0]["state"] if queues else "NOT_FOUND"
            results["batch_queue"] = {"status": "ok" if q_state == "ENABLED" else "error", "queue": cfg.batch_job_queue, "state": q_state}
        except Exception as exc:
            results["batch_queue"] = {"status": "error", "queue": cfg.batch_job_queue, "error": str(exc)}
    else:
        results["batch_queue"] = {"status": "skipped", "reason": "BATCH_JOB_QUEUE not configured"}

    print_json_result("Infrastructure Verification", results)
    any_failed = any(v["status"] == "error" for v in results.values())
    if any_failed:
        raise typer.Exit(code=1)


@app.command("batch-wait")
def batch_wait(
    job_id: str = typer.Option(..., "--job-id", help="AWS Batch job ID to poll."),
    region: str = typer.Option("us-east-1", "--region"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
    timeout_seconds: int = typer.Option(300, "--timeout", help="Maximum seconds to wait."),
    poll_interval: int = typer.Option(30, "--poll-interval", help="Seconds between polls."),
):
    """Poll an AWS Batch job until it reaches a terminal state (SUCCEEDED or FAILED).

    Exits with code 0 on SUCCEEDED, 1 on FAILED or timeout.
    Safe to run inside Docker containers.
    """
    import time
    import boto3 as _boto3

    batch = _boto3.Session(
        profile_name=profile or None,
        region_name=region,
    ).client("batch")

    elapsed = 0
    status = "UNKNOWN"
    while elapsed < timeout_seconds:
        response = batch.describe_jobs(jobs=[job_id])
        jobs = response.get("jobs", [])
        if not jobs:
            typer.echo(f"Job {job_id} not found", err=True)
            raise typer.Exit(code=1)
        status = jobs[0]["status"]
        reason = jobs[0].get("statusReason", "")
        typer.echo(f"[{elapsed}s] Job {job_id}: {status}{(' — ' + reason) if reason else ''}")
        if status == "SUCCEEDED":
            raise typer.Exit(code=0)
        if status == "FAILED":
            typer.echo(f"Job failed: {reason}", err=True)
            raise typer.Exit(code=1)
        time.sleep(poll_interval)
        elapsed += poll_interval

    typer.echo(f"Timeout after {timeout_seconds}s. Last status: {status}", err=True)
    raise typer.Exit(code=1)


@app.command("cleanup-test-resources")
def cleanup_test_resources(
    config: str = typer.Option(DEFAULT_RUNTIME_CONFIG, "--config", "-c"),
    profile: str | None = typer.Option(None, "--profile", help="AWS profile name."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print actions without executing."),
):
    """Cancel running Batch jobs and purge the SQS URL fetch queue.

    Intended to be called after a test run (including on failure) to prevent
    orphaned Batch compute and SQS messages from incurring charges.
    Safe to run inside Docker containers.
    """
    import boto3 as _boto3

    runtime = build_aws_runtime(config, aws_profile=profile)
    cfg = runtime.config
    session = _boto3.Session(
        profile_name=cfg.aws_profile or None,
        region_name=cfg.region,
    )
    actions: list[dict] = []

    # Cancel non-terminal Batch jobs
    if cfg.batch_job_queue:
        batch = session.client("batch")
        terminal = {"SUCCEEDED", "FAILED"}
        non_terminal_statuses = ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"]
        for status in non_terminal_statuses:
            paginator = batch.get_paginator("list_jobs")
            for page in paginator.paginate(jobQueue=cfg.batch_job_queue, jobStatus=status):
                for job in page.get("jobSummaryList", []):
                    job_id = job["jobId"]
                    job_name = job.get("jobName", "")
                    actions.append({"action": "cancel_batch_job", "job_id": job_id, "job_name": job_name, "status": status})
                    if not dry_run:
                        try:
                            batch.terminate_job(jobId=job_id, reason="Cancelled by cleanup-test-resources")
                        except Exception as exc:
                            actions[-1]["error"] = str(exc)
    else:
        actions.append({"action": "skip_batch_cancel", "reason": "BATCH_JOB_QUEUE not configured"})

    # Purge SQS URL fetch queue
    if cfg.url_fetch_queue_url:
        actions.append({"action": "purge_sqs_queue", "queue_url": cfg.url_fetch_queue_url})
        if not dry_run:
            try:
                session.client("sqs").purge_queue(QueueUrl=cfg.url_fetch_queue_url)
                actions[-1]["status"] = "purged"
            except Exception as exc:
                actions[-1]["status"] = "error"
                actions[-1]["error"] = str(exc)
    else:
        actions.append({"action": "skip_sqs_purge", "reason": "URL_FETCH_QUEUE_URL not configured"})

    print_json_result("Cleanup Test Resources", {"dry_run": dry_run, "actions": actions})


from eml_transformer.commands.aws_validation import register as register_aws_validation_commands


register_aws_validation_commands(app, print_json_result)


if __name__ == "__main__":
    app()
