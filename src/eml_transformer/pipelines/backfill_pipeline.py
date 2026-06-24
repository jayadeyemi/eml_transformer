from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass
from typing import Any

from tqdm.auto import tqdm


from eml_transformer.logging import silence_loggers
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
        seed_checkpoint: bool = False,
    ):
        results = []

        for source_name, source_config in source_configs.items():
            source = create_source(
                source_name,
                **source_config.get('ingestion', {}),
            )

            if not source.supports_backfill:
                continue

            results[source_name] = self.run_source(
                source_name=source_name,
                source_config=source_config,
                start_date=start_date,
                end_date=end_date,
                window_days=window_days,
                seed_checkpoint=seed_checkpoint,
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
            **source_config.get('ingestion', {}),
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

<<<<<<< HEAD
        if window_days < 1:
            raise ValueError("window_days must be greater than or equal to 1")

        if start > end:
            raise ValueError("start_date must be before or equal to end_date")
=======
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456

        windows = list(
            self._iter_date_windows(
                start=start,
                end=end,
                window_days=window_days,
            )
        )


        ingestion_results = []


        with tqdm(
            total=len(windows),
            desc=f"Backfill {source_name}",
            unit="window",
            dynamic_ncols=True,
        ) as pbar:

            for window_index, (from_date, to_date) in enumerate(windows, start=1):
                pbar.set_postfix(
                    window=f"{from_date}→{to_date}",
                    completed=f"{window_index - 1}/{len(windows)}",
                )

                with silence_loggers(
                    "eml_transformer.pipelines.ingestion_pipeline",
                    "eml_transformer.ingestion",
                ):
                    result = self.ingestion_pipeline.run_source(
                        source_name=source_name,
                        source_config=source_config,
                        from_date=from_date,
                        to_date=to_date,
                        update_checkpoint=False,
                    )

                ingestion_results.append(result)

                pbar.set_postfix(
                    window=f"{from_date}→{to_date}",
                    status=result.status,
                    fetched=result.records_fetched,
                    written=result.records_written,
                    skipped=result.records_skipped,
                    completed=f"{window_index}/{len(windows)}",
                )

                pbar.update(1)
        

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

<<<<<<< HEAD
            results.append(result)

            if result.status != "success":
                return results

        if seed_checkpoint and results:
            # Seed to the day AFTER the final window's end date so that the
            # next regular incremental run starts from the day following the
            # backfill range, not from the last backfilled day itself.
            # This prevents re-fetching the boundary day; any records on that
            # day were already ingested and deduped by hash, but double-fetching
            # wastes API quota and adds noise to run logs.
            final_end_date = date.fromisoformat(windows[-1][1])
            next_day = (final_end_date + timedelta(days=1)).isoformat()
=======
        if seed_checkpoint and ingestion_results:
            final_end = windows[-1][1]
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456

            self.ingestion_pipeline.initialize_checkpoint(
                source_name=source_name,
                checkpoint_value=next_day,
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
        if window_days < 1:
            raise ValueError("window_days must be greater than or equal to 1")

        if start > end:
            raise ValueError("start must be before or equal to end")

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
