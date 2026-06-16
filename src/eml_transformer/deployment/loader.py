from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from eml_transformer.deployment.model import DeploymentConfig


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level: {path}")

    return data


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)

    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)

    return merged


def load_deployment_config(path: str | Path) -> DeploymentConfig:
    deployment_path = Path(path).resolve()
    deployment_doc = load_yaml(deployment_path)
    repo_root = _find_repo_root(deployment_path)
    deployment_meta = deployment_doc.get("deployment", {})
    layers: list[Path] = []
    merged: dict[str, Any] = {}
    seen_layers: set[Path] = set()

    base_configs = _as_list(deployment_meta.get("base"))

    if not base_configs:
        raise ValueError(f"deployment.base is required: {deployment_path}")

    for base_config in base_configs:
        base_path = _resolve_config_path(base_config, repo_root, deployment_path.parent)
        merged = deep_merge(
            merged,
            _load_config_layer(base_path, repo_root, layers, seen_layers),
        )

    source_configs = deployment_meta.get("source_configs")

    if source_configs is None:
        source_paths = (
            []
            if "sources" in merged
            else sorted((repo_root / "configs" / "sources").glob("*.yaml"))
        )
    else:
        source_paths = [
            _resolve_config_path(item, repo_root, deployment_path.parent)
            for item in source_configs
        ]

    for source_path in source_paths:
        merged = deep_merge(
            merged,
            _load_config_layer(source_path, repo_root, layers, seen_layers),
        )

    layers.append(deployment_path)
    merged = deep_merge(merged, deployment_doc)

    return DeploymentConfig(path=deployment_path, config=merged, layers=layers)


def _resolve_config_path(value: str, repo_root: Path, relative_to: Path) -> Path:
    path = Path(value)

    if path.is_absolute():
        return path

    repo_path = (repo_root / path).resolve()

    if repo_path.exists():
        return repo_path

    return (relative_to / path).resolve()


def _load_config_layer(
    path: Path,
    repo_root: Path,
    layers: list[Path],
    seen_layers: set[Path],
) -> dict[str, Any]:
    path = path.resolve()

    if path in seen_layers:
        return {}

    seen_layers.add(path)
    doc = load_yaml(path)
    layer_meta = _layer_meta(doc)
    merged: dict[str, Any] = {}

    if isinstance(layer_meta, dict):
        for source_config in _as_list(layer_meta.get("source_configs")):
            source_path = _resolve_config_path(source_config, repo_root, path.parent)
            merged = deep_merge(
                merged,
                _load_config_layer(source_path, repo_root, layers, seen_layers),
            )

    layers.append(path)
    return deep_merge(merged, doc)


def _layer_meta(doc: dict[str, Any]) -> dict[str, Any]:
    base_meta = doc.get("base")
    return base_meta if isinstance(base_meta, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _find_repo_root(start: Path) -> Path:
    cursor = start if start.is_dir() else start.parent

    for candidate in [cursor, *cursor.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate

    return cursor
