from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import typer
from dotenv import load_dotenv

from eml_transformer.ingestion.registry import available_sources
from eml_transformer.logging import setup_logging
from eml_transformer.pipelines.backfill_pipeline import BackfillPipeline
from eml_transformer.pipelines.ingestion_pipeline import IngestionPipeline
from eml_transformer.pipelines.standardization_pipeline import StandardizationPipeline
from eml_transformer.runtime import build_runtime

load_dotenv()

app = typer.Typer()
logger = logging.getLogger(__name__)


def print_result_table(title: str, results: list[Any]) -> None:
    rows = [
        result.to_summary()
        for result in results
    ]

    if not rows:
        typer.echo(f"\n{title}: no results")
        return

    df = pd.DataFrame(rows)

    typer.echo("\n" + "=" * 100)
    typer.echo(title.upper())
    typer.echo("=" * 100)
    typer.echo(df.to_string(index=False, max_colwidth=40))
    typer.echo("=" * 100 + '\n')


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


@app.command()
def ingest(
    source: str = typer.Option("all"),
    config: str = typer.Option("configs/dev.yaml"),
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
    config: str = typer.Option("configs/dev.yaml"),
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


@app.command()
def embed(
    source: str = typer.Option("all"),
    model_name: str | None = typer.Option(None, "--model", "-m"),
    config: str = typer.Option("configs/dev.yaml"),
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
            source_configs=rt.source_configs,
        )
    else:
        get_source_config(source, rt.source_configs)

        results = [
            pipeline.run_source(
                source=source,
                embedding_config=embedding_config,
            )
        ]

    print_result_table("Embedding Results", results)


@app.command("run-all")
def run_all(
    config: str = typer.Option("configs/dev.yaml"),
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
        source_configs=rt.source_configs,
    )

    print_result_table("Embedding Results", embedding_results)


@app.command()
def backfill(
    source: str = typer.Option(..., "--source", "-s"),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    window_days: int = typer.Option(30, "--window-days"),
    config: str = typer.Option("configs/dev.yaml", "--config", "-c"),
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


if __name__ == "__main__":
    app()