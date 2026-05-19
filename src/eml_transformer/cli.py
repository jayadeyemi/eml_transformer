import os

import pandas as pd
import typer
from dotenv import load_dotenv



from eml_transformer.ingestion.registry import (
    available_sources,
)

from eml_transformer.pipelines.ingestion_pipeline import (
    IngestionPipeline,
)
from eml_transformer.pipelines.embedding_pipeline import ( 
    EmbeddingPipeline
)
from eml_transformer.pipelines.standardization_pipeline import (
    StandardizationPipeline
)

from eml_transformer.runtime import build_runtime

load_dotenv()

app = typer.Typer()

def print_ingestion_preview(
    df: pd.DataFrame,
    source: str,
    n: int = 3,
) -> None:
    typer.echo("\n" + "=" * 90)
    typer.echo(f"{source.upper()} INGESTION PREVIEW")
    typer.echo("=" * 90)

    typer.echo(f"Records retrieved: {len(df)}")
    typer.echo(f"Columns: {list(df.columns)}")

    for i, row in df.head(n).iterrows():
        typer.echo(f"\nRecord {i + 1}")

        typer.echo(
            f"Title: {row.get('title')}"
        )

        typer.echo(
            f"Published: {row.get('published_at')}"
        )

        typer.echo(
            f"URL: {row.get('url')}"
        )

        typer.echo("\nText snippet:")

        typer.echo(
            (row.get("text") or "")[:1000]
        )

        typer.echo("-" * 90)

@app.command()
def sources():
    typer.echo("Available sources:")

    for source in available_sources():
        typer.echo(f"- {source}")



@app.command()
def ingest(
    source: str = typer.Option(...),
    config: str = typer.Option("configs/dev.yaml"),
    preview: bool = typer.Option(True),
):
    rt = build_runtime(config)

    pipeline = IngestionPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )


    if source.lower() == "all":
        results = pipeline.run_all(rt.source_configs)
    else:
        source_config = rt.source_configs[source]
        results = [pipeline.run_source(source, source_config)]

    typer.echo(results)



@app.command("standardize")
def clean_standardize(
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
        source_config = rt.source_configs[source]
        results = [pipeline.run_source(source, source_config)]

    typer.echo(results)

@app.command()
def embed(
    config: str = typer.Option("configs/dev.yaml"),
):
    rt = build_runtime(config)

    pipeline = EmbeddingPipeline(
        storage=rt.storage,
        paths=rt.paths,
    )

    result = pipeline.run(rt.embeddings)

    typer.echo(result)


@app.command("run-all")
def run_all(
    config: str = typer.Option("configs/pipeline.yaml"),
):
    rt = build_runtime(config)

    IngestionPipeline(
        storage=rt.storage,
        paths=rt.paths,
        config=rt.ingestion_config,
    ).run_all(rt.source_configs)

    StandardizationPipeline(
        storage=rt.storage,
        paths=rt.paths,
        config=rt.standardization_config,
    ).run_all(rt.source_configs)

    EmbeddingPipeline(
        storage=rt.storage,
        paths=rt.paths,
        config=rt.embedding_config,
    ).run()

    typer.echo("Pipeline complete.")


if __name__ == "__main__":
    app()