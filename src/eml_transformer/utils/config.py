import os
from pathlib import Path
from typing import Any

import yaml


SOURCE_ALIASES = {
    "miso": "miso_notifications",
    "weather": "weather_alerts",
    "news": "newsapi",
}


def load_config(
    path: str | Path = "configs/dev.yaml",
) -> dict[str, Any]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg is None:
        raise ValueError(f"Config file is empty: {path}")

    return cfg


def resolve_source_name(source: str) -> str:
    source_key = source.lower()
    return SOURCE_ALIASES.get(source_key, source_key)


def build_source_config(
    source: str,
    cfg: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    source_name = resolve_source_name(source)

    sources_cfg = cfg.get("sources", {})

    if source_name not in sources_cfg:
        valid = ", ".join(sources_cfg.keys())
        raise ValueError(
            f"Unknown source: {source}. Available sources: {valid}"
        )

    source_cfg = dict(sources_cfg[source_name])
    source_cfg.pop("enabled", None)

    api_key_env = source_cfg.pop("api_key_env", None)

    if api_key_env:
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise EnvironmentError(
                f"Missing required environment variable: {api_key_env}"
            )

        source_cfg["api_key"] = api_key

    return source_name, source_cfg


def build_source_configs(
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    configs = {}

    for source_name, source_cfg in cfg.get("sources", {}).items():
        if not source_cfg.get("enabled", True):
            continue

        name, kwargs = build_source_config(source_name, cfg)
        configs[name] = kwargs

    return configs