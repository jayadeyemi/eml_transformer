import os
from pathlib import Path

import yaml


def load_ingestion_config(
    path: str = "configs/ingestion.yaml",
) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)