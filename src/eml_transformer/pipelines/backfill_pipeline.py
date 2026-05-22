from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from eml_transformer.ingestion.registry import create_source
from eml_transformer.pipelines.ingestion_pipeline import (
    IngestionPipeline,
)


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
        results = {}

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
    ):
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

        results = []

        for from_date, to_date in windows:
            result = self.ingestion_pipeline.run_source(
                source_name=source_name,
                source_kwargs=source_config,
                from_date=from_date,
                to_date=to_date,
                update_checkpoint=False,
                
            )

            results.append(result)

            if result.status != "success":
                return results

        if seed_checkpoint and results:
            final_end = windows[-1][1]

            self.ingestion_pipeline.initialize_checkpoint(
                source_name=source_name,
                checkpoint_value=final_end,
                run_id="backfill_seed",
            )

        return results

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