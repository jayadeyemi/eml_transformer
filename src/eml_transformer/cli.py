import os

import pandas as pd
import typer
from dotenv import load_dotenv

from datetime import date, datetime, timedelta
from copy import deepcopy



from eml_transformer.ingestion.registry import (
    available_sources,
)
from eml_transformer.pipelines.ingestion_pipeline import (
    IngestionPipeline,
)
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import make_storage
from eml_transformer.utils.config import load_ingestion_config
load_dotenv()

app = typer.Typer()



SOURCE_ALIASES = {
    "miso": "miso_notifications",
    "weather": "weather_alerts",
    "news": "newsapi",
}


def build_pipeline(cfg: dict) -> IngestionPipeline:
    storage = make_storage(cfg["storage"])

    paths = StoragePaths(
        root=cfg.get("paths", {}).get("root", ".")
    )

    return IngestionPipeline(
        storage=storage,
        paths=paths,
    )

def build_source_config(
    source: str,
    cfg: dict,
) -> tuple[str, dict]:
    source_key = source.lower()
    source_name = SOURCE_ALIASES.get(source_key, source_key)

    sources_cfg = cfg["sources"]

    if source_name not in sources_cfg:
        valid = ", ".join(sources_cfg.keys())
        raise typer.BadParameter(
            f"Unknown source: {source}. Available sources: {valid}"
        )

    source_cfg = dict(sources_cfg[source_name])

    source_cfg.pop("enabled", None)

    api_key_env = source_cfg.pop("api_key_env", None)

    if api_key_env:
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise typer.BadParameter(
                f"Missing required environment variable: {api_key_env}"
            )

        source_cfg["api_key"] = api_key

    return source_name, source_cfg

def build_source_configs(cfg: dict) -> dict[str, dict]:
    configs = {}

    for source_name, source_cfg in cfg["sources"].items():
        if not source_cfg.get("enabled", True):
            continue

        name, kwargs = build_source_config(source_name, cfg)
        configs[name] = kwargs

    return configs

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
def ingest(
    source: str = typer.Option(...),
    config: str = typer.Option("configs/ingestion.yaml"),
    preview: bool = typer.Option(True),
):
    cfg = load_ingestion_config(config)

    pipeline = build_pipeline(cfg)
    if source.lower() == "all":
        results = pipeline.run_all(
            build_source_configs(cfg)
        )

    else:
        source_name, source_kwargs = build_source_config(
            source,
            cfg,
        )

        results = [
            pipeline.run_source(
                source_name=source_name,
                source_kwargs=source_kwargs,
            )
        ]

    print(results)

@app.command()
def sources():
    typer.echo("Available sources:")

    for source in available_sources():
        typer.echo(f"- {source}")



def date_windows(start: date, end: date, days: int):
    cur = start

    while cur <= end:
        window_end = min(cur + timedelta(days=days - 1), end)
        yield cur.isoformat(), window_end.isoformat()
        cur = window_end + timedelta(days=1)


@app.command()
def backfill(
    source: str = typer.Option(...),
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(
        date.today().isoformat(),
        help="YYYY-MM-DD",
    ),
    window_days: int = typer.Option(7),
    config: str = typer.Option("configs/ingestion.yaml"),
):
    """
    Backfill a source over date windows.

    Example:
        eml-pipeline backfill --source news --start 2024-01-01 --end 2024-06-01
    """

    cfg = load_ingestion_config(config)
    pipeline = build_pipeline(cfg)

    source_name, base_kwargs = build_source_config(
        source,
        cfg,
    )

    if source_name not in {"newsapi"}:
        raise typer.BadParameter(
            f"Backfill is only supported for date-windowed sources for now. "
            f"Got: {source_name}"
        )

    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()

    all_results = []

    for window_start, window_end in date_windows(
        start=start_date,
        end=end_date,
        days=window_days,
    ):
        typer.echo(
            f"\nBackfilling {source_name}: {window_start} to {window_end}"
        )

        source_kwargs = deepcopy(base_kwargs)

        source_kwargs["from_date"] = window_start
        source_kwargs["to_date"] = window_end

        result = pipeline.run_source(
            source_name=source_name,
            source_kwargs=source_kwargs,
        )

        all_results.append(result)

        typer.echo(
            f"Status={result.status} | "
            f"Fetched={result.records_fetched} | "
            f"New={result.records_new} | "
            f"Skipped={result.records_skipped}"
        )

    typer.echo("\nBackfill complete.")
    typer.echo(f"Windows processed: {len(all_results)}")



if __name__ == "__main__":
    app()