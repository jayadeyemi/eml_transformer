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
    def fetch_records(self) -> Any:
        '''
        Retrieve raw records with light pre processing and store in bronze/
        '''
        pass

    @abstractmethod
    def standardize_record(self, record: dict[str, Any]) -> TextRecord:
        '''
        format raw records into standardized Textrecord data class store in silver/
        '''
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