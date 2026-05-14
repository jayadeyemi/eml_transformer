import os

import pandas as pd
import typer
from dotenv import load_dotenv

from eml_transformer.ingestion.registry import available_sources
from eml_transformer.pipelines.ingestion_pipeline import IngestionPipeline

load_dotenv()

app = typer.Typer()


SOURCE_ALIASES = {
    "miso": "miso_notifications",
    "weather": "weather_alerts",
    "news": "newsapi",
}


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
        typer.echo(f"Title: {row.get('title')}")
        typer.echo(f"Published: {row.get('published_at')}")
        typer.echo(f"URL: {row.get('url')}")
        typer.echo("\nText snippet:")
        typer.echo((row.get("text") or "")[:1000])
        typer.echo("-" * 90)


@app.command()
def ingest(
    source: str = typer.Option(..., help="Source name or alias."),
    output_dir: str = typer.Option("data/silver/text_events"),
    query: str = typer.Option('''

        (
            "Midcontinent Independent System Operator"
            OR ERCOT
            OR PJM
        )
        AND
        (
            electricity
            OR grid
            OR "power market"
            OR transmission
        )

'''),
    area: str | None = typer.Option(None),
    api_key: str | None = typer.Option(None),
    write_output: bool = typer.Option(False),
    preview: bool = typer.Option(True),
):
    source_key = source.lower()
    source_name = SOURCE_ALIASES.get(source_key, source_key)

    source_kwargs = {}

    if source_name == "newsapi":
        newsapi_key = api_key or os.getenv("NEWSAPI_KEY")

        if not newsapi_key:
            raise typer.BadParameter(
                "Missing NewsAPI key. Pass --api-key or set NEWSAPI_KEY in .env"
            )

        source_kwargs = {
            "api_key": newsapi_key,
            "query": query,
        }

    elif source_name == "weather_alerts":
        if area:
            source_kwargs = {
                "area": area,
            }

    elif source_name == "miso_notifications":
        source_kwargs = {}

    else:
        valid = ", ".join(available_sources())
        raise typer.BadParameter(
            f"Unknown source: {source}. Available sources: {valid}"
        )

    pipeline = IngestionPipeline(
        output_dir=output_dir,
        write_output=write_output,
    )

    result = pipeline.run_source(
        source_name=source_name,
        source_kwargs=source_kwargs,
    )

    if result.status == "failed":
        raise typer.Exit(f"Ingestion failed: {result.error}")

    typer.echo(f"Status: {result.status}")
    typer.echo(f"Source: {result.source}")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Records: {result.records_out}")

    if result.output_path:
        typer.echo(f"Saved to: {result.output_path}")

    if preview:
        print_ingestion_preview(result.records, source_name)


@app.command()
def sources():
    typer.echo("Available sources:")
    for source in available_sources():
        typer.echo(f"- {source}")


if __name__ == "__main__":
    app()