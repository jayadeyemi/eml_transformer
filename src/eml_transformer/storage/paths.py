from dataclasses import dataclass
from pathlib import PurePosixPath


def _clean(x: str) -> str:
    return (
        str(x)
        .strip()
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
        .replace("=", "-")
    )


def _p(*parts: str) -> str:
    return str(PurePosixPath(*parts))


@dataclass(frozen=True)
class StoragePaths:
    root: str = "data"

    # ------------------------------------------------------------------
    # Bronze
    # ------------------------------------------------------------------

    def bronze_records(
        self,
        source: str,
        # ingest_date: str,
    ) -> str:
        return _p(
            self.root,
            "bronze",
            f"source={_clean(source)}",
            # f"ingest_date={ingest_date}",
            "records.jsonl",
        )

    # ------------------------------------------------------------------
    # Silver
    # ------------------------------------------------------------------

    def silver_records(
        self,
        source: str,
        # ingest_date: str,
    ) -> str:
        return _p(
            self.root,
            "silver",
            f"source={_clean(source)}",
            # f"ingest_date={ingest_date}",
            "records.parquet",
        )

    
    # ------------------------------------------------------------------
    # Gold
    # ------------------------------------------------------------------

    def gold_records( 
        self, 
        model_name: str
    ) -> str:

        model_name = model_name.replace('sentence-transformers/', '')
        return _p(
                self.root,
                "gold",
                f"model={_clean(model_name)}",
                "embeddings.parquet" 
        ) 
        

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def dedupe_state(
        self,
        source: str,
    ) -> str:
        
        return _p(
            self.root,
            "metadata",
            "dedupe",
            f"source={_clean(source)}.json",
        )
    
    def checkpoint_key(
        self, 
        source: str
    ):
        return _p(
            self.root,
            "metadata",
            "checkpoint",
            f"source={_clean(source)}.json",
        )