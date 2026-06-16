from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


COLLECTION_SERVICES = [
    "ingest",
    "standardize",
    "embed",
    "backfill",
    "run_all",
    "gdelt_discovery",
    "url_fetch_worker",
    "s3_restore_operator",
]


@dataclass(frozen=True)
class DeploymentConfig:
    path: Path
    config: dict[str, Any]
    layers: list[Path]
