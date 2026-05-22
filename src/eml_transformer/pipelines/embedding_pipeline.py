from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage
from eml_transformer.text_processing.embeddings import (
    SentenceTransformerEmbedder
)

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    status: str

    model_name: str

    records_read: int
    embeddings_created: int
    embeddings_skipped: int
    records_failed: int = 0

    output_key: str | None = None

    error: str | None = None

    records: pd.DataFrame | None = None


class EmbeddingPipeline:
    def __init__(
        self,
        storage: Storage,
        paths: StoragePaths,
    ):
        self.storage = storage
        self.paths = paths

    def run(
        self,
        embedding_config: dict[str, Any],
        source_configs: dict[str, dict[str, Any]],
    ) -> EmbeddingResult:

        model_name = embedding_config.get(
            "model",
            "nvidia/llama-nemotron-embed-vl-1b-v2",
        )

        input_type = embedding_config.get(
            "input_type",
            "passage",
        )

        batch_size = embedding_config.get(
            "batch_size",
            32,
        )

        text_columns = embedding_config.get(
            "text_columns",
            ["title", "text"],
        )

        output_key = self.paths.gold_records(
            model_name=model_name,
        )

        sources = list(source_configs.keys())

        logger.info(
            "Starting embedding pipeline | model=%s | sources=%s",
            model_name,
            sources,
        )

        try:
            df = self._load_records(sources)

            records_read = len(df)

            if df.empty:
                return EmbeddingResult(
                    status="empty",
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

            valid_mask = (
                df["embedding_text"]
                .fillna("")
                .str.strip()
                .ne("") 
            )

            valid_df = df.loc[valid_mask].copy()

            embeddings_skipped = len(df) - len(valid_df)

            if valid_df.empty:
                return EmbeddingResult(
                    status="no_valid_text",
                    model_name=model_name,
                    records_read=records_read,
                    embeddings_created=0,
                    embeddings_skipped=embeddings_skipped,
                    output_key=output_key,
                    records=df,
                )


            client = SentenceTransformerEmbedder(
                model_name=model_name,
                device=embedding_config.get("device"),
            )

            logger.info(
                "Generating embeddings | rows=%s | batch_size=%s",
                len(valid_df),
                batch_size,
            )

            embeddings = client.embed(
                valid_df["embedding_text"].tolist(),
                batch_size=batch_size,
            )

            valid_df["embedding"] = embeddings
            valid_df["embedding_model"] = model_name
            valid_df["embedding_input_type"] = input_type

            logger.info(
                "Writing embeddings | rows=%s | output_key=%s",
                len(valid_df),
                output_key,
            )

            self.storage.write_parquet(
                valid_df,
                output_key,
            )

            return EmbeddingResult(
                status="success",
                model_name=model_name,
                records_read=records_read,
                embeddings_created=len(valid_df),
                embeddings_skipped=embeddings_skipped,
                output_key=output_key,
                records=valid_df,
            )

        except Exception as exc:
            logger.exception(
                "Embedding pipeline failed"
            )

            return EmbeddingResult(
                status="failed",
                model_name=model_name,
                records_read=0,
                embeddings_created=0,
                embeddings_skipped=0,
                output_key=output_key,
                error=str(exc),
            )

    def _load_records(
        self,
        sources: list[str],
    ) -> pd.DataFrame:
        dfs = []

        for source in sources:
            if not source == 'iem_afos':
                continue
            
            key = self.paths.silver_records(source)

            df = self.storage.read_parquet(key)

            if df.empty:
                logger.warning(
                    "No silver records found | source=%s",
                    source,
                )
                continue

            dfs.append(df)

        if not dfs:
            return pd.DataFrame()

        merged = pd.concat(
            dfs,
            ignore_index=True,
        )

        merged = (
            merged
            .drop_duplicates(subset=["record_id"])
            .sort_values(by=["published_at"])
        )

        return merged

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