from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass
from typing import Any

from eml_transformer.ingestion.registry import create_source
from eml_transformer.pipelines.ingestion_pipeline import (
    IngestionPipeline,
)

@dataclass
class BackfillResult:
    status: str
    source: str
    start_date: str
    end_date: str
    window_days: int
    windows_total: int
    windows_completed: int
    records_fetched: int
    records_written: int
    records_skipped: int
    records_failed: int = 0
    error: str | None = None

    def to_summary(self) -> dict[str, object]:
        summary = {
            "source": self.source,
            "status": self.status,
            "start": self.start_date,
            "end": self.end_date,
            "windows": f"{self.windows_completed}/{self.windows_total}",
            "fetched": self.records_fetched,
            "written": self.records_written,
            "skipped": self.records_skipped,
            "failed": self.records_failed,
        }

        if self.error:
            summary["error"] = self.error

        return summary
    
class BackfillPipeline:
    def __init__(
        self,
        ingestion_pipeline: IngestionPipeline,
    ):
        self.ingestion_pipeline = ingestion_pipeline

    def run_all(
        self,
        source_configs: dict[str, dict[str, Any]],
        start_date: str,
        end_date: str,
        window_days: int = 30,
    ):
        results = []

        for source_name, source_config in source_configs.items():
            source = create_source(
                source_name,
                **source_config,
            )

            if not source.supports_backfill:
                continue

            results[source_name] = self.run_source(
                source_name=source_name,
                source_config=source_config,
                start_date=start_date,
                end_date=end_date,
                window_days=window_days,
            )

        return results

    def run_source(
        self,
        source_name: str,
        source_config: dict[str, Any],
        start_date: str,
        end_date: str,
        window_days: int = 30,
        seed_checkpoint: bool = False,
    ) -> BackfillResult:
        source = create_source(
            source_name,
            **source_config,
        )

        if source.update_mode != "incremental":
            raise ValueError(
                f"Source does not support backfill "
                f"(update_mode={source.update_mode}): "
                f"{source_name}"
            )

        if not source.supports_backfill:
            raise ValueError(
                f"Source explicitly disables backfill: "
                f"{source_name}"
            )

        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        windows = list(
            self._iter_date_windows(
                start=start,
                end=end,
                window_days=window_days,
            )
        )

        ingestion_results = []

        for from_date, to_date in windows:
            result = self.ingestion_pipeline.run_source(
                source_name=source_name,
                source_kwargs=source_config,
                from_date=from_date,
                to_date=to_date,
                update_checkpoint=False,
            )

            ingestion_results.append(result)

            if result.status != "success":
                return self._summarize_backfill(
                    source_name=source_name,
                    start_date=start_date,
                    end_date=end_date,
                    window_days=window_days,
                    windows_total=len(windows),
                    ingestion_results=ingestion_results,
                    status="failed",
                    error=result.error,
                )

        if seed_checkpoint and ingestion_results:
            final_end = windows[-1][1]

            self.ingestion_pipeline.initialize_checkpoint(
                source_name=source_name,
                checkpoint_value=final_end,
                run_id="backfill_seed",
            )

        return self._summarize_backfill(
            source_name=source_name,
            start_date=start_date,
            end_date=end_date,
            window_days=window_days,
            windows_total=len(windows),
            ingestion_results=ingestion_results,
            status="success",
        )

    def _summarize_backfill(
        self,
        source_name: str,
        start_date: str,
        end_date: str,
        window_days: int,
        windows_total: int,
        ingestion_results: list[Any],
        status: str,
        error: str | None = None,
    ) -> BackfillResult:
        return BackfillResult(
            status=status,
            source=source_name,
            start_date=start_date,
            end_date=end_date,
            window_days=window_days,
            windows_total=windows_total,
            windows_completed=len(ingestion_results),
            records_fetched=sum(
                result.records_fetched
                for result in ingestion_results
            ),
            records_written=sum(
                result.records_written
                for result in ingestion_results
            ),
            records_skipped=sum(
                result.records_skipped
                for result in ingestion_results
            ),
            records_failed=sum(
                getattr(result, "records_failed", 0)
                for result in ingestion_results
            ),
            error=error,
        )
    @staticmethod
    def _iter_date_windows(
        start: date,
        end: date,
        window_days: int,
    ):
        current = start

        while current <= end:
            window_end = min(
                current + timedelta(days=window_days - 1),
                end,
            )

            yield (
                current.isoformat(),
                window_end.isoformat(),
            )

            current = window_end + timedelta(days=1)