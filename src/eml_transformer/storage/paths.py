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
        ingest_date: str,
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
        ingest_date: str,
    ) -> str:
        return _p(
            self.root,
            "silver",
            "text_records",
            f"source={_clean(source)}",
            # f"ingest_date={ingest_date}",
            "records.csv",
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