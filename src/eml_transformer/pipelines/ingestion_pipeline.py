from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

import eml_transformer.ingestion.sources  # noqa: F401
from eml_transformer.ingestion.registry import create_source
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage
from eml_transformer.utils.stamping import stable_hash

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    status: str
    source: str
    run_id: str
    records_fetched: int
    records_new: int
    records_skipped: int
    records_out: int
    records_failed: int = 0
    bronze_key: str | None = None
    silver_key: str | None = None
    dedupe_key: str | None = None
    error: str | None = None
    records: pd.DataFrame | None = None


class IngestionPipeline:
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
    ) -> list[IngestionResult]:
        return [
            self.run_source(source_name, source_kwargs)
            for source_name, source_kwargs in source_configs.items()
        ]

    def run_source(
        self,
        source_name: str,
        source_kwargs: dict[str, Any],
    ) -> IngestionResult:
        run_time = datetime.now(timezone.utc)
        run_id = run_time.strftime("%Y%m%dT%H%M%SZ")

        bronze_key: str | None = None
        silver_key: str | None = None
        dedupe_key: str | None = None

        try:
            source = create_source(source_name, **source_kwargs)
            run_time = datetime.now(timezone.utc)

            run_id = run_time.strftime("%Y%m%dT%H%M%SZ")

            ingest_date = run_time.strftime("%Y-%m-%d")
            bronze_key = self.paths.bronze_records(
                source=source.name,
                ingest_date=ingest_date,
            )

            silver_key = self.paths.silver_records(
                source=source.name,
                ingest_date=ingest_date,
            )
            dedupe_key = self.paths.dedupe_state(source.name)

            raw = source.fetch_raw()
            raw_records = source.parse_records(raw)

            seen = self._load_seen(dedupe_key)
            new_seen = set(seen)

            bronze_rows: list[dict[str, Any]] = []
            silver_records = []
            failed_records = 0

            for raw_record in raw_records:
                try:
                    text_record = source.standardize_record(raw_record)

                    h = stable_hash(raw_record)
                    unique_key = f"{text_record.record_id}:{h}"

                    if unique_key in seen:
                        continue

                    bronze_rows.append(
                        {
                            "source": source.name,
                            "record_id": text_record.record_id,
                            "content_hash": h,
                            "dedupe_key": unique_key,
                            "retrieved_at": run_time.isoformat(),
                            "run_id": run_id,
                            "raw": raw_record,
                        }
                    )

                    silver_records.append(text_record)
                    new_seen.add(unique_key)

                except Exception:
                    failed_records += 1
                    logger.exception(
                        "Failed to standardize raw record | source=%s",
                        source.name,
                    )

            if bronze_rows:
                self.storage.append_jsonl(bronze_rows, bronze_key)

            df_new = self._records_to_dataframe(silver_records)

            if not df_new.empty:
                self._upsert_silver(df_new, silver_key)

            self._save_seen(dedupe_key, new_seen)

            return IngestionResult(
                status="success",
                source=source.name,
                run_id=run_id,
                records_fetched=len(raw_records),
                records_new=len(bronze_rows),
                records_skipped=len(raw_records) - len(bronze_rows) - failed_records,
                records_out=len(df_new),
                records_failed=failed_records,
                bronze_key=bronze_key,
                silver_key=silver_key,
                dedupe_key=dedupe_key,
                records=df_new,
            )

        except Exception as e:
            logger.exception(
                "Ingestion failed | source=%s run_id=%s",
                source_name,
                run_id,
            )

            return IngestionResult(
                status="failed",
                source=source_name,
                run_id=run_id,
                records_fetched=0,
                records_new=0,
                records_skipped=0,
                records_out=0,
                error=str(e),
                bronze_key=bronze_key,
                silver_key=silver_key,
                dedupe_key=dedupe_key,
            )

    def _records_to_dataframe(self, records: list[Any]) -> pd.DataFrame:
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

    def _load_seen(self, key: str) -> set[str]:
        if not self.storage.exists(key):
            return set()

        state = self.storage.read_json(key)
        return set(state.get("seen", []))

    def _save_seen(self, key: str, seen: set[str]) -> None:
        self.storage.write_json(
            {
                "seen": sorted(seen),
                "count": len(seen),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            key,
        )

    def _upsert_silver(
        self,
        df_new: pd.DataFrame,
        key: str,
    ) -> None:
        if self.storage.exists(key):
            df_old = self.storage.csv(key)

            df = pd.concat(
                [df_old, df_new],
                ignore_index=True,
            )

            df = df.drop_duplicates(
                subset=["record_id"],
                keep="last",
            )
        else:
            df = df_new

        self.storage.write_csv(df, key)