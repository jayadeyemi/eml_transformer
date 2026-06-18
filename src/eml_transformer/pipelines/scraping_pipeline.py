from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
import pandas as pd

import eml_transformer.ingestion.sources  # noqa: F401
from eml_transformer.ingestion.registry import create_source
from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage

from eml_transformer.extraction.scraper import (
    ArticleScraperConfig,
    HybridArticleScraper,
)

logger = logging.getLogger(__name__)


NON_RETRYABLE_STATUSES = {
    "success",
    "forbidden",
    "not_found",
}


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
        logger.info("Starting scraping for %s sources", len(source_configs))

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

        input_key: str | None = None
        output_key: str | None = None

        try:
            source = create_source(
                source_name,
                **source_kwargs.get("ingestion", {}),
            )

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

            input_df = self.storage.read_parquet(input_key)

            if input_df.empty:
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

            existing_df = self._load_existing_output(output_key)

            to_scrape_df = self._select_records_to_scrape(
                input_df=input_df,
                existing_df=existing_df,
            )

            if to_scrape_df.empty:
                return ScrapingResult(
                    status="up_to_date",
                    source=source.name,
                    input_artifact=input_artifact,
                    output_artifact=output_artifact,
                    records_read=len(input_df),
                    records_out=len(existing_df),
                    input_key=input_key,
                    output_key=output_key,
                    records=existing_df,
                )

            scraped_df = asyncio.run(
                self._scrape_dataframe_async(
                    df=to_scrape_df,
                    scraping_config=scraping_config,
                )
            )

            final_df = pd.concat(
                [existing_df, scraped_df],
                ignore_index=True,
            )

            final_df = (
                final_df
                .drop_duplicates(subset=["record_id"], keep="last")
                .reset_index(drop=True)
            )

            self.storage.write_parquet(final_df, output_key)

            records_failed = (
                int(final_df["scrape_status"].ne("success").sum())
                if "scrape_status" in final_df.columns
                else 0
            )

            logger.info(
                "Scraping complete | source=%s | read=%s | scraped=%s | total=%s | output=%s",
                source.name,
                len(input_df),
                len(scraped_df),
                len(final_df),
                output_key,
            )

            return ScrapingResult(
                status="success",
                source=source.name,
                input_artifact=input_artifact,
                output_artifact=output_artifact,
                records_read=len(input_df),
                records_out=len(final_df),
                records_failed=records_failed,
                input_key=input_key,
                output_key=output_key,
                records=final_df,
            )

        except Exception as exc:
            logger.exception("Scraping failed | source=%s", source_name)

            return ScrapingResult(
                status="failed",
                source=source_name,
                input_artifact=input_artifact,
                output_artifact=output_artifact,
                records_read=0,
                records_out=0,
                records_failed=0,
                input_key=input_key,
                output_key=output_key,
                error=str(exc),
            )

    async def _scrape_dataframe_async(
        self,
        df: pd.DataFrame,
        scraping_config: dict[str, Any],
    ) -> pd.DataFrame:
        request_timeout = scraping_config.get("request_timeout", 15)
        playwright_timeout = scraping_config.get("playwright_timeout", 30_000)

        scraper = HybridArticleScraper(
            ArticleScraperConfig(
                request_timeout=request_timeout,
                playwright_timeout=playwright_timeout,
                fallback_on_forbidden=True,
            )
        )

        rows = []

        async with aiohttp.ClientSession() as session:
            for _, record in df.iterrows():
                record_dict = record.to_dict()

                result = await scraper.scrape(
                    session=session,
                    url=record_dict["url"],
                )

                rows.append(
                    {
                        **record_dict,
                        **result,
                    }
                )

        return pd.DataFrame(rows)

    def _load_existing_output(self, output_key: str) -> pd.DataFrame:
        if not self.storage.exists(output_key):
            return pd.DataFrame()

        return self.storage.read_parquet(output_key)

    def _select_records_to_scrape(
        self,
        input_df: pd.DataFrame,
        existing_df: pd.DataFrame,
    ) -> pd.DataFrame:
        if "record_id" not in input_df.columns:
            raise ValueError("Scraping input must contain a 'record_id' column.")

        if "url" not in input_df.columns:
            raise ValueError("Scraping input must contain a 'url' column.")

        input_df = input_df.drop_duplicates(
            subset=["record_id"],
            keep="last",
        ).copy()

        if existing_df.empty or "record_id" not in existing_df.columns:
            return input_df

        processed_record_ids = set(existing_df["record_id"].dropna())

        return input_df.loc[
            ~input_df["record_id"].isin(processed_record_ids)
        ].copy()