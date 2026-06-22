
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
    embedding_source_configs: dict[str, dict]

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
    embedding_source_configs = _build_embedding_source_configs(cfg, source_configs)

    return Runtime(
        cfg=cfg,
        storage=storage,
        paths=paths,
        source_configs=source_configs,
        embedding_source_configs=embedding_source_configs,
    )


def _build_embedding_source_configs(
    cfg: dict[str, Any],
    source_configs: dict[str, dict],
) -> dict[str, dict]:
    configs: dict[str, dict] = dict(source_configs)

    for source_name, source_cfg in cfg.get("sources", {}).items():
        if not source_cfg.get("enabled", True):
            continue

        if "embedding_input" not in source_cfg:
            continue

        embedding_cfg = dict(source_cfg)
        embedding_cfg.pop("enabled", None)
        configs[source_name] = embedding_cfg

    return configs
