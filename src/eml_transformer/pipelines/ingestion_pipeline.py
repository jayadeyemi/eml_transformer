from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

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
    records_written: int
    records_skipped: int = 0
    records_failed: int = 0
    bronze_key: str | None = None
    dedupe_key: str | None = None
    error: str | None = None

    def to_summary(self) -> dict[str, object]:
        summary = {
            "source": self.source,
            "status": self.status,
            "fetched": self.records_fetched,
            "written": self.records_written,
            "skipped": self.records_skipped,
            "failed": self.records_failed,
        }

        if self.error:
            summary["error"] = self.error

        return summary


class IngestionPipeline:
    def __init__(self, storage: Storage, paths: StoragePaths):
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
        from_date: str | None = None,
        to_date: str | None = None,
        update_checkpoint: bool = True,
    ) -> IngestionResult:
        run_time = datetime.now(timezone.utc)
        run_id = run_time.strftime("%Y%m%dT%H%M%SZ")

        bronze_key: str | None = None
        dedupe_key: str | None = None

        try:
            source = create_source(source_name, **source_kwargs)

            bronze_key = self.paths.bronze_records(source.name)
            dedupe_key = self.paths.dedupe_state(source.name)

            effective_from_date = from_date

            if source.update_mode == "incremental":
                checkpoint = self._load_checkpoint(source.name)

                if effective_from_date is None and checkpoint is not None:
                    effective_from_date = checkpoint.get("last_checkpoint_value")

                if effective_from_date is None:
                    lookback_days = getattr(source, "default_lookback_days", 7)

                    effective_from_date = (
                        run_time - timedelta(days=lookback_days)
                    ).date().isoformat()

                if effective_from_date is None:
                    raise ValueError(
                        f"No from_date, checkpoint, or default_start_date found for "
                        f"incremental source {source.name}"
                    )

            logger.info(
                "Fetching raw records | source=%s | update_mode=%s | from=%s | to=%s",
                source.name,
                source.update_mode,
                effective_from_date,
                to_date,
            )

            raw_records = source.fetch_records(
                from_date=effective_from_date,
                to_date=to_date,
            )

            seen_hashes = self._load_seen(dedupe_key)
            bronze_rows = []

            for raw_record in raw_records:
                raw_hash = stable_hash(raw_record)

                if raw_hash in seen_hashes:
                    continue

                bronze_rows.append(
                    {
                        "source": source.name,
                        "run_id": run_id,
                        "retrieved_at": run_time.isoformat(),
                        "raw_record_hash": raw_hash,
                        "raw": raw_record,
                    }
                )

                seen_hashes.add(raw_hash)

            records_written = len(bronze_rows)
            records_skipped = len(raw_records) - records_written

            if bronze_rows:
                self.storage.append_jsonl(
                    bronze_key,
                    bronze_rows,
                )

            self._save_seen(dedupe_key, seen_hashes)

            should_update_checkpoint = (
                source.update_mode == "incremental"
                and update_checkpoint
                and from_date is None
                and to_date is None
                and raw_records
            )

            if should_update_checkpoint:
                self._update_checkpoint(
                    source=source,
                    run_id=run_id,
                    raw_records=raw_records,
                )

            return IngestionResult(
                status="success",
                source=source.name,
                run_id=run_id,
                records_fetched=len(raw_records),
                records_written=records_written,
                records_skipped=records_skipped,
                bronze_key=bronze_key,
                dedupe_key=dedupe_key,
            )

        except Exception as e:
            logger.exception(
                "Ingestion failed | source=%s | run_id=%s",
                source_name,
                run_id,
            )

            return IngestionResult(
                status="failed",
                source=source_name,
                run_id=run_id,
                records_fetched=0,
                records_written=0,
                records_skipped=0,
                error=str(e),
                bronze_key=bronze_key,
                dedupe_key=dedupe_key,
            )

    def _update_checkpoint(
        self,
        source: Any,
        run_id: str,
        raw_records: list[dict[str, Any]],
    ) -> None:
        checkpoint_values: list[datetime] = []

        for record in raw_records:
            try:
                value = source.get_checkpoint_value(record)

                if value is None:
                    continue

                if isinstance(value, str):
                    value = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )

                if not isinstance(value, datetime):
                    raise TypeError(
                        f"Checkpoint value must be datetime or ISO string, "
                        f"got {type(value)}"
                    )

                if value.tzinfo is None:
                    raise ValueError(
                        f"Checkpoint datetime is timezone-naive: {value}"
                    )

                value = value.astimezone(timezone.utc)

                checkpoint_values.append(value)

            except Exception:
                logger.warning(
                    "Skipping malformed checkpoint value | source=%s",
                    source.name,
                    exc_info=True,
                )

        if not checkpoint_values:
            logger.info(
                "No valid checkpoint values found | source=%s",
                source.name,
            )
            return

        last_checkpoint_value = max(checkpoint_values)

        self._save_checkpoint(
            source.name,
            {
                "source": source.name,
                "last_successful_run_id": run_id,
                "last_checkpoint_value": last_checkpoint_value.isoformat(),
            },
        )

        logger.info(
            "Checkpoint updated | source=%s | value=%s",
            source.name,
            last_checkpoint_value.isoformat(),
        )
        
    def initialize_checkpoint(
        self,
        source_name: str,
        checkpoint_value: str,
        run_id: str = "manual_init",
    ) -> None:
        self._save_checkpoint(
            source_name,
            {
                "source": source_name,
                "last_successful_run_id": run_id,
                "last_checkpoint_value": checkpoint_value,
            },
        )
    def _load_checkpoint(self, key: str) -> dict[str, Any] | None:
        checkpoint_key = self.paths.checkpoint_key(key)

        if not self.storage.exists(checkpoint_key):
            return None

        return self.storage.read_json(checkpoint_key)

    def _save_checkpoint(
        self,
        key: str,
        checkpoint: dict[str, Any],
    ) -> None:
        checkpoint_key = self.paths.checkpoint_key(key)

        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()

        self.storage.write_json(
            checkpoint,
            checkpoint_key,
        )

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