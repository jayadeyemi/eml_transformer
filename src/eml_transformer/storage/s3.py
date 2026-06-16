from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import json
import pickle
import uuid

import pandas as pd

from eml_transformer.storage.base import Storage


@dataclass
class S3Storage(Storage):
    bucket: str
    prefix: str = ""
    region: Optional[str] = None
    profile: Optional[str] = None
    endpoint_url: Optional[str] = None
    _fs: Any = field(init=False, repr=False, default=None)

    def _init_fs(self) -> None:
        if self._fs is not None:
            return

        try:
            import s3fs
        except ImportError as exc:
            raise ImportError(
                "S3Storage requires the optional dependency 's3fs'. "
                "Install project dependencies or run: python -m pip install s3fs"
            ) from exc

        client_kwargs = {}
        if self.region:
            client_kwargs["region_name"] = self.region
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url

        self._fs = s3fs.S3FileSystem(
            profile=self.profile,
            client_kwargs=client_kwargs,
        )

    def _key(self, key: str) -> str:
        key = key.lstrip("/")
        pref = self.prefix.strip("/")
        return f"{pref}/{key}" if pref else key

    def object_key(self, key: str) -> str:
        return self._key(key)

    def _uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"

    def exists(self, key: str) -> bool:
        self._init_fs()
        p = f"{self.bucket}/{self._key(key)}"
        try:
            info = self._fs.info(p)
            return info.get("type") == "file"
        except FileNotFoundError:
            return False

    def list(self, prefix: str) -> list[str]:
        self._init_fs()
        key_prefix = self._key(prefix).rstrip("/")
        root = (
            f"{self.bucket}/{key_prefix}"
            if key_prefix
            else f"{self.bucket}/{self.prefix.strip('/')}".rstrip("/")
        )

        try:
            paths = self._fs.find(root)
        except FileNotFoundError:
            return []

        out = []
        for p in paths:
            if p.startswith(f"{self.bucket}/"):
                k = p[len(self.bucket) + 1 :]
            else:
                k = p

            pref = self.prefix.strip("/")
            if pref and k.startswith(pref + "/"):
                k = k[len(pref) + 1 :]

            out.append(k)

        return sorted(out)

    def read_parquet(self, key: str) -> pd.DataFrame:
        self._init_fs()
        return pd.read_parquet(
            self._uri(key),
            engine="pyarrow",
            filesystem=self._fs,
            partitioning=None,
        )

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"
        df.to_parquet(tmp_uri, index=True, engine="pyarrow", filesystem=self._fs)
        self._copy_tmp(tmp_key, key)

    def read_csv(self, key: str) -> pd.DataFrame:
        self._init_fs()
        return pd.read_csv(
            self._uri(key),
            storage_options={"profile": self.profile} if self.profile else None,
        )

    def write_csv(self, df: pd.DataFrame, key: str, index: bool = False) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"
        df.to_csv(
            tmp_uri,
            index=index,
            storage_options={"profile": self.profile} if self.profile else None,
        )
        self._copy_tmp(tmp_key, key)

    def read_json(self, key: str) -> Any:
        self._init_fs()
        with self._fs.open(self._uri(key), "r") as f:
            return json.load(f)

    def write_json(self, obj: Any, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        with self._fs.open(f"s3://{self.bucket}/{tmp_key}", "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=str)
        self._copy_tmp(tmp_key, key)

    def read_bytes(self, key: str) -> bytes:
        self._init_fs()
        with self._fs.open(self._uri(key), "rb") as f:
            return f.read()

    def write_bytes(self, data: bytes, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        with self._fs.open(f"s3://{self.bucket}/{tmp_key}", "wb") as f:
            f.write(data)
        self._copy_tmp(tmp_key, key)

    def write_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        with self._fs.open(f"s3://{self.bucket}/{tmp_key}", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")
        self._copy_tmp(tmp_key, key)

    def append_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        if not self.exists(key):
            self.write_bytes(b"", key)

        self.write_jsonl(f"{key}.parts/{uuid.uuid4().hex}.jsonl", rows)

    def read_jsonl(self, key: str) -> list[dict[str, Any]]:
        self._init_fs()

        if not self.exists(key):
            return []

        rows: list[dict[str, Any]] = []
        with self._fs.open(self._uri(key), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))

        for part_key in self.list(f"{key}.parts/"):
            with self._fs.open(self._uri(part_key), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))

        return rows

    def read_pickle(self, key: str) -> Any:
        self._init_fs()
        with self._fs.open(self._uri(key), "rb") as f:
            return pickle.load(f)

    def write_pickle(self, obj: Any, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        with self._fs.open(f"s3://{self.bucket}/{tmp_key}", "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        self._copy_tmp(tmp_key, key)

    def _copy_tmp(self, tmp_key: str, final_key: str) -> None:
        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(final_key)}"
        self._fs.copy(src, dst)
        try:
            self._fs.rm(src)
        except Exception:
            pass
