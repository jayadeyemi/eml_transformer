
from dataclasses import dataclass
from typing import Any

from eml_transformer.storage.paths import StoragePaths
from eml_transformer.storage.storage import Storage, make_storage
from eml_transformer.utils.config import (
    build_source_configs,
    load_config,
)


@dataclass
class Runtime:
    cfg: dict
    storage: Storage
    paths: StoragePaths
    source_configs: dict[str, dict]

    @property
    def source_names(self) -> list[str]:
        return list(self.source_configs.keys())

    @property
    def ingestion_config(self) -> dict:
        return self.cfg.get("ingestion", {})

    @property
    def standardization_config(self) -> dict:
        return self.cfg.get("standardization", {})

    @property
    def embedding_config(self) -> dict:
        return self.cfg.get("embeddings", {})


def build_runtime(config_path: str) -> Runtime:
    cfg = load_config(config_path)

    storage = make_storage(cfg["storage"])

    paths = StoragePaths(
        root=cfg.get("paths", {}).get("root", ".")
    )

    source_configs = build_source_configs(cfg)

    return Runtime(
        cfg=cfg,
        storage=storage,
        paths=paths,
        source_configs=source_configs,
    )