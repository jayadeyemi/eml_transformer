from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage
from eml_transformer.text_processing.embeddings import SentenceTransformerEmbedder

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    status: str
    source: str
    model_name: str

    records_read: int
    embeddings_created: int
    embeddings_skipped: int
    records_failed: int = 0

    output_key: str | None = None
    error: str | None = None
    records: pd.DataFrame | None = None

    def to_summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "read": self.records_read,
            "embedded": self.embeddings_created,
            "skipped": self.embeddings_skipped,
            "failed": self.records_failed,
            "model": self.model_name,
            "output": self.output_key,
            "error": self.error,
        }


class EmbeddingPipeline:
    def __init__(
        self,
        storage: Storage,
        paths: StoragePaths,
    ):
        self.storage = storage
        self.paths = paths

    def run_all(
        self,
        embedding_config: dict[str, Any],
        source_configs: dict[str, dict[str, Any]],
    ) -> list[EmbeddingResult]:
        results = []

        for source, source_config in source_configs.items():
            result = self.run_source(
                source=source,
                embedding_config=embedding_config,
<<<<<<< HEAD
                source_config=source_config,
=======
                source_config=source_configs[source],
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456
            )
            results.append(result)

        return results

    def run_source(
        self,
        source: str,
        embedding_config: dict[str, Any],
<<<<<<< HEAD
        source_config: dict[str, Any] | None = None,
=======
        source_config: dict[str, Any],
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456
    ) -> EmbeddingResult:
        model_name = embedding_config.get(
            "model",
            "nvidia/llama-nemotron-embed-vl-1b-v2",
        )

        input_type = embedding_config.get("input_type", "passage")
        batch_size = embedding_config.get("batch_size", 32)
        text_columns = embedding_config.get("text_columns", ["title", "text"])

        output_key = self.paths.gold_records(
            source=source,
            model_name=model_name,
        )

        logger.info(
            "Starting embedding pipeline | source=%s | model=%s",
            source,
            model_name,
        )

        try:
<<<<<<< HEAD
            df = self._load_source_records(source, source_config or {})
=======
            df = self._load_source_records(source, source_config)
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456
            records_read = len(df)

            if df.empty:
                return EmbeddingResult(
                    status="empty",
                    source=source,
                    model_name=model_name,
                    records_read=0,
                    embeddings_created=0,
                    embeddings_skipped=0,
                    output_key=output_key,
                    records=df,
                )

            df["embedding_text"] = df.apply(
                lambda row: self._build_embedding_text(
                    row=row.to_dict(),
                    text_columns=text_columns,
                ),
                axis=1,
            )

            valid_mask = df["embedding_text"].fillna("").str.strip().ne("")
            valid_df = df.loc[valid_mask].copy()

            invalid_text_count = len(df) - len(valid_df)

            if valid_df.empty:
                return EmbeddingResult(
                    status="no_valid_text",
                    source=source,
                    model_name=model_name,
                    records_read=records_read,
                    embeddings_created=0,
                    embeddings_skipped=invalid_text_count,
                    output_key=output_key,
                    records=df,
                )

            existing_df = self._load_existing_embeddings(output_key)

            existing_record_ids = (
                set(existing_df["record_id"])
                if not existing_df.empty and "record_id" in existing_df.columns
                else set()
            )

            new_df = valid_df.loc[
                ~valid_df["record_id"].isin(existing_record_ids)
            ].copy()

            already_embedded_count = len(valid_df) - len(new_df)
            embeddings_skipped = invalid_text_count + already_embedded_count

            if new_df.empty:
                return EmbeddingResult(
                    status="up_to_date",
                    source=source,
                    model_name=model_name,
                    records_read=records_read,
                    embeddings_created=0,
                    embeddings_skipped=embeddings_skipped,
                    output_key=output_key,
                    records=existing_df,
                )

            client = SentenceTransformerEmbedder(
                model_name=model_name,
                device=embedding_config.get("device"),
            )

            logger.info(
                "Generating embeddings | source=%s | rows=%s | batch_size=%s",
                source,
                len(new_df),
                batch_size,
            )

            embeddings = client.embed(
                new_df["embedding_text"].tolist(),
                batch_size=batch_size,
            )

            new_df["embedding"] = embeddings
            new_df["embedding_model"] = model_name
            new_df["embedding_input_type"] = input_type
            new_df["source"] = source

            final_df = pd.concat(
                [existing_df, new_df],
                ignore_index=True,
            )

            final_df = final_df.drop_duplicates(
                subset=["record_id"],
                keep="last",
            )

            logger.info(
                "Writing embeddings | source=%s | rows=%s | output_key=%s",
                source,
                len(final_df),
                output_key,
            )

            self.storage.write_parquet(
                final_df,
                output_key,
            )

            return EmbeddingResult(
                status="success",
                source=source,
                model_name=model_name,
                records_read=records_read,
                embeddings_created=len(new_df),
                embeddings_skipped=embeddings_skipped,
                output_key=output_key,
                records=new_df,
            )

        except Exception as exc:
            logger.exception(
                "Embedding pipeline failed | source=%s",
                source,
            )

            return EmbeddingResult(
                status="failed",
                source=source,
                model_name=model_name,
                records_read=0,
                embeddings_created=0,
                embeddings_skipped=0,
                output_key=output_key,
                error=str(exc),
            )

    def _load_source_records(
        self,
        source: str,
        source_config: dict[str, Any],
    ) -> pd.DataFrame:
        input_artifact = source_config.get("embedding_input", "records")
<<<<<<< HEAD
        key = self.paths.silver_records(source, name=input_artifact)

        if not self.storage.exists(key):
            logger.warning(
                "No silver records found | source=%s | input_artifact=%s | key=%s",
                source,
                input_artifact,
                key,
            )
            return pd.DataFrame()
=======

        key = self.paths.silver_records(
            source=source,
            name=input_artifact,
        )
>>>>>>> c941e2473b28ab31e2d773e3333b64827fb2d456

        df = self.storage.read_parquet(key)

        if df.empty:
            logger.warning(
                "No silver records found | source=%s | input_artifact=%s",
                source,
                input_artifact,
            )
            return pd.DataFrame()

        df = (
            df
            .drop_duplicates(subset=["record_id"])
            .sort_values(by=["published_at"])
            .reset_index(drop=True)
        )

        return df

    def _load_existing_embeddings(
        self,
        output_key: str,
    ) -> pd.DataFrame:
        try:
            return self.storage.read_parquet(output_key)
        except FileNotFoundError:
            return pd.DataFrame()

    def _build_embedding_text(
        self,
        row: dict[str, Any],
        text_columns: list[str],
    ) -> str:
        parts = []

        for column in text_columns:
            value = row.get(column)

            if value is None:
                continue

            value = str(value).strip()

            if value:
                parts.append(value)

        return "\n\n".join(parts)
