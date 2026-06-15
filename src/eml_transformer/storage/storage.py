from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import json
import pickle
import uuid

from eml_transformer.logging import get_logger

logger = get_logger(__name__)


import pandas as pd


class Storage:
    def exists(self, key: str) -> bool:

        logger.debug(f"checking file path: {key}")
        raise NotImplementedError
    
    def list(self, prefix: str) -> list[str]:
        """
        List keys under a prefix (non-recursive or recursive depending on backend).

        Returns keys relative to storage root (same format used in read/write).
        """
        raise NotImplementedError

    def read_parquet(self, key: str) -> pd.DataFrame:
        logger.debug(f"reading file: {key}")
        raise NotImplementedError

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        logger.info(f"Writing {len(df)} rows to {key}")
        raise NotImplementedError

    def read_csv(self, key: str) -> pd.DataFrame:
        logger.debug(f"reading csv file: {key}")
        raise NotImplementedError

    def write_csv(
        self,
        df: pd.DataFrame,
        key: str,
        index: bool = False,
    ) -> None:
        logger.info(f"Writing {len(df)} rows to csv {key}")
        raise NotImplementedError
    

    def read_json(self, key: str) -> Any:
        logger.debug(f"reading file: {key}")
        raise NotImplementedError

    def write_json(self, obj: Any, key: str) -> None:
        logger.info(f"Writing json to {key}")
        raise NotImplementedError

    def read_bytes(self, key: str) -> bytes:
        logger.debug(f"reading bytes from file: {key}")
        raise NotImplementedError

    def write_bytes(self, data: bytes, key: str) -> None:
        logger.info(f"Writing {len(data)} bytes to {key}")
        raise NotImplementedError

    def write_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        logger.info(f"Writing {len(rows)} rows to jsonl {key}")
        raise NotImplementedError

    def append_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        logger.info(f"Appending {len(rows)} rows to jsonl {key}")
        raise NotImplementedError

    def read_jsonl(self, key: str) -> list[dict[str, Any]]:
        logger.debug(f"reading jsonl file: {key}")
        raise NotImplementedError
    

    def read_pickle(self, key):
        logger.info(f"reading file:{key}")
        raise NotImplementedError
    
    def write_pickle(self, obj: Any, key: str):
        logger.info(f"Writing Pickle to {key}")
        raise NotImplementedError



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
                # make both absolute → safe
                rel = p.resolve().relative_to(base)
                out.append(str(rel).replace("\\", "/"))

        return sorted(out)

    # =====================
    # Parquet
    # =====================
    def read_parquet(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(self._path(key))

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.to_parquet(tmp, index=True)
        tmp.replace(path)
    

    # =====================
    # CSV
    # =====================

    def read_csv(self, key: str) -> pd.DataFrame:
        return pd.read_csv(self._path(key))


    def write_csv(
        self,
        df: pd.DataFrame,
        key: str,
        index: bool = False,
    ) -> None:
        path = self._path(key)

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        tmp = path.with_suffix(
            path.suffix + ".tmp"
        )

        df.to_csv(
            tmp,
            index=index,
        )

        tmp.replace(path)
    # =====================
    # JSON
    # =====================
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
        """Append rows to a JSONL file atomically.

        Reads the existing file (if any), appends new rows, then
        writes atomically via a .tmp file + rename so a crash or
        interrupt never leaves a partially-written file.
        """
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

    def read_jsonl(
        self,
        key: str,
    ) -> list[dict[str, Any]]:
        path = self._path(key)

        if not path.exists():
            return []

        rows = []

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                rows.append(json.loads(line))

        return rows
    
    
    # =====================
    # Pickle
    # =====================
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


@dataclass
class S3Storage(Storage):
    """
    S3-backed storage using s3fs/fsspec under the hood.

    - key is a path relative to (bucket, prefix)
      e.g. key="data/bronze/equities.parquet"
    - best-effort "atomic" write:
        write to temp key -> copy to final -> delete temp
      (S3 doesn't support true atomic rename)
    """
    bucket: str
    prefix: str = ""

    # credential/config controls (optional)
    region: Optional[str] = None
    profile: Optional[str] = None
    endpoint_url: Optional[str] = None  # for MinIO/localstack if needed

    # internal cached fs (don’t pass in init)
    _fs: Any = field(init=False, repr=False, default=None)

    def _init_fs(self):
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

        # profile works locally (shared credentials); on ECS you typically won't set it
        self._fs = s3fs.S3FileSystem(profile=self.profile, client_kwargs=client_kwargs)

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
        p = f"{self.bucket}/{self._key(key)}"   # NO s3://
        try:
            info = self._fs.info(p)             # raises if missing
            return info.get("type") == "file"
        except FileNotFoundError:
            return False
    

    def list(self, prefix: str) -> list[str]:
        self._init_fs()

        key_prefix = self._key(prefix).rstrip("/")
        root = f"{self.bucket}/{key_prefix}" if key_prefix else f"{self.bucket}/{self.prefix.strip('/')}".rstrip("/")
        # s3fs.find expects "bucket/prefix" (no s3://)
        try:
            paths = self._fs.find(root)
        except FileNotFoundError:
            return []

        out = []
        for p in paths:
            # p is like "bucket/prefix/...."
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
        # pandas will use s3fs via fsspec if installed
        return pd.read_parquet(
            self._uri(key),
            engine="pyarrow",
            filesystem=self._fs,
            partitioning=None,   # <-- disables hive inference
        )

    def write_parquet(self, df: pd.DataFrame, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        df.to_parquet(tmp_uri, index=True, engine="pyarrow", filesystem=self._fs)

        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"
        self._fs.copy(src, dst)

        try:
            self._fs.rm(src)
        except Exception:
            pass
    
    # =====================
    # CSV
    # =====================

    def read_csv(self, key: str) -> pd.DataFrame:
        self._init_fs()

        return pd.read_csv(
            self._uri(key),
            storage_options={
                "profile": self.profile,
            } if self.profile else None,
        )


    def write_csv(
        self,
        df: pd.DataFrame,
        key: str,
        index: bool = False,
    ) -> None:
        self._init_fs()

        tmp_key = (
            f"{self._key(key)}"
            f".__tmp__{uuid.uuid4().hex}"
        )

        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        df.to_csv(
            tmp_uri,
            index=index,
            storage_options={
                "profile": self.profile,
            } if self.profile else None,
        )

        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"

        self._fs.copy(src, dst)

        try:
            self._fs.rm(src)
        except Exception:
            pass

    def read_json(self, key: str) -> Any:
        self._init_fs()
        uri = self._uri(key)
        with self._fs.open(uri, "r") as f:
            return json.load(f)

    def write_json(self, obj: Any, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        with self._fs.open(tmp_uri, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=str)

        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"
        self._fs.copy(src, dst)

        try:
            self._fs.rm(src)
        except Exception:
            pass

    def read_bytes(self, key: str) -> bytes:
        self._init_fs()

        with self._fs.open(self._uri(key), "rb") as f:
            return f.read()

    def write_bytes(self, data: bytes, key: str) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        with self._fs.open(tmp_uri, "wb") as f:
            f.write(data)

        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"
        self._fs.copy(src, dst)

        try:
            self._fs.rm(src)
        except Exception:
            pass

    def write_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        self._init_fs()
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        with self._fs.open(tmp_uri, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str))
                f.write("\n")

        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"
        self._fs.copy(src, dst)

        try:
            self._fs.rm(src)
        except Exception:
            pass

    def append_jsonl(self, key: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        if not self.exists(key):
            self.write_bytes(b"", key)

        part_key = f"{key}.parts/{uuid.uuid4().hex}.jsonl"
        self.write_jsonl(part_key, rows)

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
        """
        Read a python object serialized via pickle from S3.
        """
        self._init_fs()
        uri = self._uri(key)
        with self._fs.open(uri, "rb") as f:
            return pickle.load(f)

    def write_pickle(self, obj: Any, key: str) -> None:
        """
        Write a python object to S3 using pickle, with best-effort atomic write:
        write temp -> copy to final -> delete temp.
        """
        self._init_fs()

        # temp key alongside final
        tmp_key = f"{self._key(key)}.__tmp__{uuid.uuid4().hex}"
        tmp_uri = f"s3://{self.bucket}/{tmp_key}"

        # write temp
        with self._fs.open(tmp_uri, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)

        # copy temp -> final (overwrite)
        src = f"{self.bucket}/{tmp_key}"
        dst = f"{self.bucket}/{self._key(key)}"
        self._fs.copy(src, dst)

        # cleanup temp
        try:
            self._fs.rm(src)
        except Exception:
            pass


def make_storage(cfg: dict) -> Storage:
    s = cfg
    backend = s["backend"].lower()

    if backend == "local":
        return LocalStorage(base_dir=Path(s["base_dir"]))

    if backend == "s3":
        bucket = s["bucket"]
        prefix = s.get("prefix", "")
        region = s.get("region")
        profile = s.get("profile")
        endpoint_url = s.get("endpoint_url")

        logger.info(
            "Initializing S3Storage | bucket=%s prefix=%r region=%s profile=%s endpoint=%s",
            bucket,
            prefix,
            region,
            profile,
            endpoint_url,
        )

        return S3Storage(
            bucket=bucket,
            prefix=prefix,
            region=region,
            profile=profile,
            endpoint_url=endpoint_url,
        )

    raise ValueError(f"Unknown storage.backend={backend!r}")
