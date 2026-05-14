from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import eml_transformer.ingestion.sources  # noqa: F401 - triggers registry
from eml_transformer.ingestion.registry import create_source


logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    source: str
    run_id: str
    status: str
    records: pd.DataFrame
    records_out: int
    output_path: Path | None
    started_at: datetime
    finished_at: datetime
    error: str | None = None


class IngestionPipeline:
    """
    Orchestrates ingestion for one or more text sources.

    Source classes handle:
        - fetch_raw()
        - parse_records()
        - standardize_record()

    This pipeline handles:
        - creating sources from registry
        - running sources
        - logging
        - writing results
        - returning run metadata
    """

    def __init__(
        self,
        output_dir: str | Path = "data/silver/text_events",
        write_output: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.write_output = write_output

    def run_source(
        self,
        source_name: str,
        source_kwargs: dict[str, Any] | None = None,
    ) -> IngestionResult:
        source_kwargs = source_kwargs or {}

        started_at = datetime.now(timezone.utc)
        run_id = started_at.strftime("%Y%m%dT%H%M%SZ")

        logger.info(
            "Starting ingestion for source=%s run_id=%s",
            source_name,
            run_id,
        )

        try:
            source = create_source(source_name, **source_kwargs)
            df = source.run()

            output_path = None

            if self.write_output:
                output_path = self._write_output(
                    df=df,
                    source_name=source_name,
                    run_id=run_id,
                )

            finished_at = datetime.now(timezone.utc)

            logger.info(
                "Finished ingestion for source=%s run_id=%s records=%s",
                source_name,
                run_id,
                len(df),
            )

            return IngestionResult(
                source=source_name,
                run_id=run_id,
                status="success",
                records=df,
                records_out=len(df),
                output_path=output_path,
                started_at=started_at,
                finished_at=finished_at,
            )

        except Exception as exc:
            finished_at = datetime.now(timezone.utc)

            logger.exception(
                "Ingestion failed for source=%s run_id=%s",
                source_name,
                run_id,
            )

            return IngestionResult(
                source=source_name,
                run_id=run_id,
                status="failed",
                records=pd.DataFrame(),
                records_out=0,
                output_path=None,
                started_at=started_at,
                finished_at=finished_at,
                error=str(exc),
            )

    def run_many(
        self,
        sources: dict[str, dict[str, Any] | None],
    ) -> list[IngestionResult]:
        results: list[IngestionResult] = []

        for source_name, source_kwargs in sources.items():
            result = self.run_source(
                source_name=source_name,
                source_kwargs=source_kwargs or {},
            )
            results.append(result)

        return results

    def _write_output(
        self,
        df: pd.DataFrame,
        source_name: str,
        run_id: str,
    ) -> Path:
        output_path = (
            self.output_dir
            / f"source={source_name}"
            / f"run_id={run_id}"
            / "records.parquet"
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.drop(columns=['text', 'raw']).to_csv(output_path, index=False)

        logger.info("Wrote ingestion output to %s", output_path)

        return output_path