from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    status: str

    model_name: str

    records_read: int
    embeddings_created: int
    embeddings_skipped: int
    records_failed: int = 0

    embeddings_key: str | None = None
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
    ) -> EmbeddingResult:
        raise NotImplementedError

    def _load_model(
        self,
        model_name: str,
    ):
        raise NotImplementedError

    def _build_embedding_text(
        self,
        row: dict[str, Any],
    ) -> str:
        raise NotImplementedError