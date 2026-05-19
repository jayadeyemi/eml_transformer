from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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
    ) -> IngestionResult:
        run_time = datetime.now(timezone.utc)
        run_id = run_time.strftime("%Y%m%dT%H%M%SZ")

        bronze_key: str | None = None
        dedupe_key: str | None = None

        try:
            source = create_source(source_name, **source_kwargs)

            bronze_key = self.paths.bronze_records(source.name)
            dedupe_key = self.paths.dedupe_state(source.name)

            raw = source.fetch_raw()
            raw_records = source.parse_records(raw)

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

            if bronze_rows:
                self.storage.append_jsonl(bronze_rows, bronze_key)

            self._save_seen(dedupe_key, seen_hashes)

            return IngestionResult(
                status="success",
                source=source.name,
                run_id=run_id,
                records_fetched=len(raw_records),
                records_written=len(bronze_rows),
                records_skipped=len(raw_records) - len(bronze_rows),
                bronze_key=bronze_key,
                dedupe_key=dedupe_key,
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
                records_written=0,
                records_skipped=0,
                error=str(e),
                bronze_key=bronze_key,
                dedupe_key=dedupe_key,
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