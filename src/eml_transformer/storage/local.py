from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import pickle

import pandas as pd

from eml_transformer.storage.base import Storage


@dataclass(frozen=True)
class LocalStorage(Storage):
    base_dir: Path

    def _path(self, key: str) -> Path:
        return (self.base_dir / key).resolve()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list(self, prefix: str) -> list[str]:
        base = self.base_dir.resolve()
        path = (base / prefix).resolve()

        if not path.exists():
            return []

        if path.is_file():
            return [prefix]

        out = []
        for p in path.rglob("*"):
            if p.is_file():
                rel = p.resolve().relative_to(base)
                out.append(str(rel).replace("\\", "/"))

        return sorted(out)

    def read_parquet(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(self._path(key))

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.to_parquet(tmp, index=True)
        tmp.replace(path)

    def read_csv(self, key: str) -> pd.DataFrame:
        return pd.read_csv(self._path(key))

    def write_csv(self, df: pd.DataFrame, key: str, index: bool = False) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.to_csv(tmp, index=index)
        tmp.replace(path)

    def read_json(self, key: str) -> Any:
        path = self._path(key)
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, obj: Any, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=str)
        tmp.replace(path)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, data: bytes, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def write_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")

        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")

        tmp.replace(path)

    def append_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_lines: list[bytes] = []

        if path.exists():
            with path.open("rb") as f:
                existing_lines = f.readlines()

        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for line in existing_lines:
                decoded = line.decode("utf-8")
                f.write(decoded if decoded.endswith("\n") else decoded + "\n")
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")
        tmp.replace(path)

    def read_jsonl(self, key: str) -> list[dict[str, Any]]:
        path = self._path(key)

        if not path.exists():
            return []

        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))

        return rows

    def read_pickle(self, key: str) -> Any:
        path = self._path(key)
        with path.open("rb") as f:
            return pickle.load(f)

    def write_pickle(self, obj: Any, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
