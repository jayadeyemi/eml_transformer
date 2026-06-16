from __future__ import annotations

from typing import Any

import pandas as pd

from eml_transformer.logging import get_logger


logger = get_logger(__name__)


class Storage:
    def exists(self, key: str) -> bool:
        logger.debug("checking file path: %s", key)
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:
        raise NotImplementedError

    def read_parquet(self, key: str) -> pd.DataFrame:
        logger.debug("reading parquet file: %s", key)
        raise NotImplementedError

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        logger.info("Writing %s rows to parquet %s", len(df), key)
        raise NotImplementedError

    def read_csv(self, key: str) -> pd.DataFrame:
        logger.debug("reading csv file: %s", key)
        raise NotImplementedError

    def write_csv(self, df: pd.DataFrame, key: str, index: bool = False) -> None:
        logger.info("Writing %s rows to csv %s", len(df), key)
        raise NotImplementedError

    def read_json(self, key: str) -> Any:
        logger.debug("reading json file: %s", key)
        raise NotImplementedError

    def write_json(self, obj: Any, key: str) -> None:
        logger.info("Writing json to %s", key)
        raise NotImplementedError

    def read_bytes(self, key: str) -> bytes:
        logger.debug("reading bytes from %s", key)
        raise NotImplementedError

    def write_bytes(self, data: bytes, key: str) -> None:
        logger.info("Writing %s bytes to %s", len(data), key)
        raise NotImplementedError

    def write_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        logger.info("Writing %s rows to jsonl %s", len(rows), key)
        raise NotImplementedError

    def append_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        logger.info("Appending %s rows to jsonl %s", len(rows), key)
        raise NotImplementedError

    def read_jsonl(self, key: str) -> list[dict[str, Any]]:
        logger.debug("reading jsonl file: %s", key)
        raise NotImplementedError

    def read_pickle(self, key: str) -> Any:
        logger.info("reading pickle file: %s", key)
        raise NotImplementedError

    def write_pickle(self, obj: Any, key: str) -> None:
        logger.info("Writing pickle to %s", key)
        raise NotImplementedError
