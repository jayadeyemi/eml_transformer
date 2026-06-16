from __future__ import annotations

from pathlib import Path

from eml_transformer.logging import get_logger
from eml_transformer.storage.base import Storage
from eml_transformer.storage.local import LocalStorage
from eml_transformer.storage.s3 import S3Storage


logger = get_logger(__name__)


def make_storage(cfg: dict) -> Storage:
    backend = cfg["backend"].lower()

    if backend == "local":
        return LocalStorage(base_dir=Path(cfg["base_dir"]))

    if backend == "s3":
        logger.info(
            "Initializing S3Storage | bucket=%s prefix=%r region=%s profile=%s endpoint=%s",
            cfg["bucket"],
            cfg.get("prefix", ""),
            cfg.get("region"),
            cfg.get("profile"),
            cfg.get("endpoint_url"),
        )
        return S3Storage(
            bucket=cfg["bucket"],
            prefix=cfg.get("prefix", ""),
            region=cfg.get("region"),
            profile=cfg.get("profile"),
            endpoint_url=cfg.get("endpoint_url"),
        )

    raise ValueError(f"Unknown storage.backend={backend!r}")
