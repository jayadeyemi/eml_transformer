from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

import eml_transformer.ingestion.sources  # noqa: F401
from eml_transformer.ingestion.registry import create_source
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class ScrapingResult:
    status: str
    source: str

    input_artifact: str
    output_artifact: str

    records_read: int
    records_out: int
    records_failed: int = 0

    input_key: str | None = None
    output_key: str | None = None

    error: str | None = None
    records: pd.DataFrame | None = None

    def to_summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "input_artifact": self.input_artifact,
            "output_artifact": self.output_artifact,
            "read": self.records_read,
            "out": self.records_out,
            "failed": self.records_failed,
            "input": self.input_key,
            "output": self.output_key,
            "error": self.error,
        }


class ScrapingPipeline:
    DEFAULT_INPUT_ARTIFACT = "records"
    DEFAULT_OUTPUT_ARTIFACT = "extracted_articles"

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
    ) -> list[ScrapingResult]:
        logger.info(
            "Starting scraping for %s sources",
            len(source_configs),
        )

        results = []

        for source_name, source_kwargs in source_configs.items():
            if not source_kwargs.get("enabled", True):
                continue

            scraping_config = source_kwargs.get("scraping", {})

            if not scraping_config.get("enabled", False):
                continue

            results.append(
                self.run_source(
                    source_name=source_name,
                    source_kwargs=source_kwargs,
                )
            )

        logger.info("Scraping complete")

        return results

    def run_source(
        self,
        source_name: str,
        source_kwargs: dict[str, Any],
    ) -> ScrapingResult:
        scraping_config = source_kwargs.get("scraping", {})

        input_artifact = scraping_config.get(
            "input",
            self.DEFAULT_INPUT_ARTIFACT,
        )

        output_artifact = scraping_config.get(
            "output",
            self.DEFAULT_OUTPUT_ARTIFACT,
        )

        try:
            source = create_source(source_name, **source_kwargs)

            input_key = self.paths.silver_records(
                source=source.name,
                name=input_artifact,
            )

            output_key = self.paths.silver_records(
                source=source.name,
                name=output_artifact,
            )

            logger.info(
                "Starting scraping | source=%s | input=%s | output=%s",
                source.name,
                input_key,
                output_key,
            )

            if not self.storage.exists(input_key):
                logger.warning(
                    "No scraping input found | source=%s | input=%s",
                    source.name,
                    input_key,
                )

                return ScrapingResult(
                    status="skipped",
                    source=source.name,
                    input_artifact=input_artifact,
                    output_artifact=output_artifact,
                    records_read=0,
                    records_out=0,
                    input_key=input_key,
                    output_key=output_key,
                    error=f"No scraping input found: {input_key}",
                )

            input_df = self.storage.read(input_key)

            if input_df.empty:
                logger.warning(
                    "Scraping input is empty | source=%s | input=%s",
                    source.name,
                    input_key,
                )

                return ScrapingResult(
                    status="skipped",
                    source=source.name,
                    input_artifact=input_artifact,
                    output_artifact=output_artifact,
                    records_read=0,
                    records_out=0,
                    input_key=input_key,
                    output_key=output_key,
                    records=input_df,
                    error="Scraping input is empty",
                )

            # output_df = self._scrape_dataframe(
            #     df=input_df,
            #     source=source,
            #     source_kwargs=source_kwargs,
            # )
            output_df = None

            self.storage.write(
                key=output_key,
                df=output_df,
            )

            logger.info(
                "Scraping complete | source=%s | read=%s | out=%s | output=%s",
                source.name,
                len(input_df),
                len(output_df),
                output_key,
            )

            return ScrapingResult(
                status="success",
                source=source.name,
                input_artifact=input_artifact,
                output_artifact=output_artifact,
                records_read=len(input_df),
                records_out=len(output_df),
                input_key=input_key,
                output_key=output_key,
                records=output_df,
            )

        except Exception as exc:
            logger.exception(
                "Scraping failed | source=%s",
                source_name,
            )

            return ScrapingResult(
                status="failed",
                source=source_name,
                input_artifact=input_artifact,
                output_artifact=output_artifact,
                records_read=0,
                records_out=0,
                records_failed=0,
                input_key=None,
                output_key=None,
                error=str(exc),
            )

