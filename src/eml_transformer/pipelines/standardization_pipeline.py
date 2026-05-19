from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

import eml_transformer.ingestion.sources  # noqa: F401
from eml_transformer.ingestion.registry import create_source
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage
from eml_transformer.text_processing.cleaning import clean_text

logger = logging.getLogger(__name__)


@dataclass
class StandardizationResult:
    status: str
    source: str

    records_read: int
    records_out: int
    records_failed: int = 0

    bronze_key: str | None = None
    silver_key: str | None = None

    error: str | None = None
    records: pd.DataFrame | None = None

class StandardizationPipeline:
    def __init__(
        self,
        storage: Storage,
        paths: StoragePaths,
    ):
        self.storage = storage
        self.paths = paths

    def run_all(
        self,
        source_configs: dict[str, dict],
    ) -> list[StandardizationResult]:
        return [
            self.run_source(source_name, source_kwargs)
            for source_name, source_kwargs in source_configs.items()
        ]

    def run_source(
        self,
        source_name: str,
        source_kwargs: dict[str, Any],
    ) -> StandardizationResult:
        bronze_key: str | None = None
        silver_key: str | None = None

        try:
            source = create_source(source_name, **source_kwargs)

            bronze_key = self.paths.bronze_records(
                source=source.name,
            )

            silver_key = self.paths.silver_records(
                source=source.name,
            )

            if not self.storage.exists(bronze_key):
                return StandardizationResult(
                    status="skipped",
                    source=source.name,
                    records_read=0,
                    records_out=0,
                    bronze_key=bronze_key,
                    silver_key=silver_key,
                    error=f"No bronze data found for source: {source.name}",
                )

            bronze_rows = self.storage.read_jsonl(bronze_key)

            records = []
            failed_records = 0

            for row in bronze_rows:
                try:
                    raw_record = row["raw"]

                    text_record = source.standardize_record(
                        raw_record
                    )

                    text_record = self._clean_record(
                        text_record
                    )

                    records.append(text_record)

                except Exception:
                    failed_records += 1

                    logger.exception(
                        "Failed to standardize record | source=%s",
                        source.name,
                    )

            df = self._records_to_dataframe(records)
            df = self._deduplicate(df)

            if not df.empty:
                self.storage.write_csv(df, silver_key)

            return StandardizationResult(
                status="success",
                source=source.name,
                records_read=len(bronze_rows),
                records_out=len(df),
                records_failed=failed_records,
                bronze_key=bronze_key,
                silver_key=silver_key,
                records=df,
            )

        except Exception as e:
            logger.exception(
                "Standardization failed | source=%s",
                source_name,
            )

            return StandardizationResult(
                status="failed",
                source=source_name,
                records_read=0,
                records_out=0,
                error=str(e),
                bronze_key=bronze_key,
                silver_key=silver_key,
            )

    def _clean_record(
        self,
        record,
    ):
        data = record.model_dump()

        title = data.get("title") or ""
        text = data.get("text") or ""

        title = clean_text(title)
        text = clean_text(text)

        if data.get("source") == "weather_alerts":
            text = remove_weather_boilerplate(text)

        data["title"] = title
        data["text"] = text

        return record.__class__(**data)

    def _records_to_dataframe(
        self,
        records: list[Any],
    ) -> pd.DataFrame:
        if not records:
            return pd.DataFrame()

        rows = []

        for record in records:
            if hasattr(record, "model_dump"):
                rows.append(record.model_dump())
            elif hasattr(record, "dict"):
                rows.append(record.dict())
            else:
                rows.append(record)

        return pd.DataFrame(rows)

    def _deduplicate(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        if df.empty:
            return df

        if "record_id" not in df.columns:
            return df.drop_duplicates()

        return df.drop_duplicates(
            subset=["record_id"],
            keep="last",
        ).reset_index(drop=True)