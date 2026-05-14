from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from eml_transformer.ingestion.schema import (
    TextRecord,
    TEXT_RECORD_COLUMNS,
)

import hashlib

class TextSource(ABC):
    """
    Base class for textual ingestion sources.
    """

    name: str
    source_type: str

    @abstractmethod
    def fetch_raw(self) -> Any:
        pass

    @abstractmethod
    def parse_records(self, raw: Any) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def standardize_record(self, record: dict[str, Any]) -> TextRecord:
        pass

    def validate_schema(self, df: pd.DataFrame) -> None:
        missing = [
            col for col in TEXT_RECORD_COLUMNS
            if col not in df.columns
        ]

        if missing:
            raise ValueError(
                f"{self.name} missing required columns: {missing}"
            )

    def run(self) -> pd.DataFrame:
        raw = self.fetch_raw()
        parsed = self.parse_records(raw)

        records = [
            self.standardize_record(record).to_dict()
            for record in parsed
        ]

        df = pd.DataFrame(records)

        for col in TEXT_RECORD_COLUMNS:
            if col not in df.columns:
                df[col] = None

        df = df[TEXT_RECORD_COLUMNS]

        self.validate_schema(df)

        return df
    
    def _make_record_id(
        self,
        *parts: str | None,
    ) -> str:
        """
        Generate deterministic record ID from stable fields.
        """

        key = "|".join(
            str(part).strip()
            for part in parts
            if part is not None
        )

        return hashlib.sha256(
            key.encode("utf-8")
        ).hexdigest()