from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd

from eml_transformer.storage.base import Storage


JSON_FORMATS = {"json", "jsonl", "jsonl.gz"}
TRANSFER_JSON_SUFFIXES = (".json", ".jsonl", ".jsonl.gz")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str) -> str:
    return (
        str(value)
        .strip()
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
        .replace("=", "-")
    )


def _p(*parts: str) -> str:
    return str(PurePosixPath(*[str(part).strip("/") for part in parts if str(part)]))


def _infer_format(key: str) -> str | None:
    if key.endswith(".jsonl.gz"):
        return "jsonl.gz"
    if key.endswith(".jsonl"):
        return "jsonl"
    if key.endswith(".json"):
        return "json"
    if key.endswith(".parquet"):
        return "parquet"
    return None


@dataclass(frozen=True)
class TransferAggregatePart:
    key: str
    records: int
    format: str


@dataclass(frozen=True)
class TransferAggregateResult:
    name: str
    run_id: str
    source_prefix: str
    output_prefix: str
    manifest_key: str
    input_files: int
    records: int
    parts: list[TransferAggregatePart] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "run_id": self.run_id,
            "source_prefix": self.source_prefix,
            "output_prefix": self.output_prefix,
            "manifest": self.manifest_key,
            "input_files": self.input_files,
            "records": self.records,
            "parts": len(self.parts),
            "skipped_files": len(self.skipped_files),
        }


class TransferAggregator:
    """
    Build coarse transfer artifacts from many small storage objects.

    The canonical data layout remains unchanged. Aggregate outputs are intended
    for filesystem-to-filesystem moves such as SLATE to S3 or S3 to local
    scratch, where copying thousands of small objects is slower and less
    reliable than copying fewer compressed transfer parts.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    def aggregate_json_records(
        self,
        source_prefix: str,
        name: str,
        run_id: str | None = None,
        output_prefix: str = "transfer/aggregates",
        manifest_prefix: str = "manifests/transfer_aggregates",
        target_rows: int = 10_000,
        envelope: bool = True,
        max_files: int | None = None,
    ) -> TransferAggregateResult:
        if target_rows < 1:
            raise ValueError("target_rows must be greater than or equal to 1")

        run_id = run_id or self._new_run_id()
        keys = self._source_keys(
            source_prefix=source_prefix,
            output_prefix=output_prefix,
            manifest_prefix=manifest_prefix,
            max_files=max_files,
        )
        records: list[dict[str, Any]] = []
        parts: list[TransferAggregatePart] = []
        source_files: list[dict[str, Any]] = []
        skipped_files: list[str] = []
        total_records = 0

        for key in keys:
            detected_format = _infer_format(key)

            if detected_format not in JSON_FORMATS:
                skipped_files.append(key)
                continue

            file_records = list(
                self._iter_json_records(
                    key=key,
                    detected_format=detected_format,
                    envelope=envelope,
                )
            )
            source_files.append(
                {
                    "key": key,
                    "format": detected_format,
                    "records": len(file_records),
                }
            )

            for record in file_records:
                records.append(record)
                total_records += 1

                if len(records) >= target_rows:
                    parts.append(
                        self._write_json_part(
                            records=records,
                            name=name,
                            run_id=run_id,
                            output_prefix=output_prefix,
                        )
                    )
                    records = []

        if records:
            parts.append(
                self._write_json_part(
                    records=records,
                    name=name,
                    run_id=run_id,
                    output_prefix=output_prefix,
                )
            )

        manifest_key = self._manifest_key(
            manifest_prefix=manifest_prefix,
            name=name,
            run_id=run_id,
        )
        result = TransferAggregateResult(
            name=name,
            run_id=run_id,
            source_prefix=source_prefix,
            output_prefix=output_prefix,
            manifest_key=manifest_key,
            input_files=len(source_files),
            records=total_records,
            parts=parts,
            skipped_files=skipped_files,
        )
        self._write_manifest(
            result=result,
            aggregate_format="jsonl.gz",
            source_files=source_files,
            target_rows=target_rows,
            envelope=envelope,
        )
        return result

    def aggregate_parquet_records(
        self,
        source_prefix: str,
        name: str,
        run_id: str | None = None,
        output_prefix: str = "transfer/aggregates",
        manifest_prefix: str = "manifests/transfer_aggregates",
        target_rows: int = 250_000,
        max_files: int | None = None,
    ) -> TransferAggregateResult:
        if target_rows < 1:
            raise ValueError("target_rows must be greater than or equal to 1")

        run_id = run_id or self._new_run_id()
        keys = self._source_keys(
            source_prefix=source_prefix,
            output_prefix=output_prefix,
            manifest_prefix=manifest_prefix,
            max_files=max_files,
        )
        frames: list[pd.DataFrame] = []
        parts: list[TransferAggregatePart] = []
        source_files: list[dict[str, Any]] = []
        skipped_files: list[str] = []
        buffered_rows = 0
        total_records = 0

        for key in keys:
            if _infer_format(key) != "parquet":
                skipped_files.append(key)
                continue

            df = self.storage.read_parquet(key).copy()
            df["_transfer_source_key"] = key
            frames.append(df)
            buffered_rows += len(df)
            total_records += len(df)
            source_files.append(
                {
                    "key": key,
                    "format": "parquet",
                    "records": len(df),
                }
            )

            if buffered_rows >= target_rows:
                parts.extend(
                    self._write_parquet_parts(
                        frames=frames,
                        name=name,
                        run_id=run_id,
                        output_prefix=output_prefix,
                        target_rows=target_rows,
                    )
                )
                frames = []
                buffered_rows = 0

        if frames:
            parts.extend(
                self._write_parquet_parts(
                    frames=frames,
                    name=name,
                    run_id=run_id,
                    output_prefix=output_prefix,
                    target_rows=target_rows,
                )
            )

        manifest_key = self._manifest_key(
            manifest_prefix=manifest_prefix,
            name=name,
            run_id=run_id,
        )
        result = TransferAggregateResult(
            name=name,
            run_id=run_id,
            source_prefix=source_prefix,
            output_prefix=output_prefix,
            manifest_key=manifest_key,
            input_files=len(source_files),
            records=total_records,
            parts=parts,
            skipped_files=skipped_files,
        )
        self._write_manifest(
            result=result,
            aggregate_format="parquet",
            source_files=source_files,
            target_rows=target_rows,
            envelope=False,
        )
        return result

    def _source_keys(
        self,
        source_prefix: str,
        output_prefix: str,
        manifest_prefix: str,
        max_files: int | None,
    ) -> list[str]:
        keys = [
            key
            for key in self.storage.list(source_prefix)
            if not key.startswith(output_prefix.rstrip("/") + "/")
            and not key.startswith(manifest_prefix.rstrip("/") + "/")
        ]
        keys = sorted(keys)
        return keys[:max_files] if max_files is not None else keys

    def _iter_json_records(
        self,
        key: str,
        detected_format: str,
        envelope: bool,
    ) -> Iterable[dict[str, Any]]:
        raw_bytes = self.storage.read_bytes(key)

        if detected_format == "jsonl.gz":
            text = gzip.decompress(raw_bytes).decode("utf-8")
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif detected_format == "jsonl":
            text = raw_bytes.decode("utf-8")
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif detected_format == "json":
            rows = [json.loads(raw_bytes.decode("utf-8"))]
        else:
            raise ValueError(f"Unsupported JSON transfer format: {detected_format}")

        for idx, row in enumerate(rows):
            if envelope:
                yield {
                    "source_key": key,
                    "source_record_index": idx,
                    "payload": row,
                }
                continue

            if isinstance(row, dict):
                yield {
                    **row,
                    "_transfer_source_key": key,
                    "_transfer_source_record_index": idx,
                }
            else:
                yield {
                    "_transfer_source_key": key,
                    "_transfer_source_record_index": idx,
                    "value": row,
                }

    def _write_json_part(
        self,
        records: list[dict[str, Any]],
        name: str,
        run_id: str,
        output_prefix: str,
    ) -> TransferAggregatePart:
        part_key = self._part_key(
            output_prefix=output_prefix,
            name=name,
            run_id=run_id,
            suffix="jsonl.gz",
        )
        content = "".join(
            json.dumps(record, ensure_ascii=False, default=str) + "\n"
            for record in records
        ).encode("utf-8")
        self.storage.write_bytes(gzip.compress(content), part_key)
        return TransferAggregatePart(
            key=part_key,
            records=len(records),
            format="jsonl.gz",
        )

    def _write_parquet_parts(
        self,
        frames: list[pd.DataFrame],
        name: str,
        run_id: str,
        output_prefix: str,
        target_rows: int,
    ) -> list[TransferAggregatePart]:
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        parts: list[TransferAggregatePart] = []

        for start in range(0, len(df), target_rows):
            chunk = df.iloc[start : start + target_rows].reset_index(drop=True)
            part_key = self._part_key(
                output_prefix=output_prefix,
                name=name,
                run_id=run_id,
                suffix="parquet",
            )
            self.storage.write_parquet(chunk, part_key)
            parts.append(
                TransferAggregatePart(
                    key=part_key,
                    records=len(chunk),
                    format="parquet",
                )
            )

        return parts

    def _write_manifest(
        self,
        result: TransferAggregateResult,
        aggregate_format: str,
        source_files: list[dict[str, Any]],
        target_rows: int,
        envelope: bool,
    ) -> None:
        self.storage.write_json(
            {
                "kind": "transfer_aggregate",
                "created_at": _utc_iso(),
                "name": result.name,
                "run_id": result.run_id,
                "source_prefix": result.source_prefix,
                "output_prefix": result.output_prefix,
                "aggregate_format": aggregate_format,
                "target_rows": target_rows,
                "envelope": envelope,
                "input_files": result.input_files,
                "records": result.records,
                "parts": [
                    {
                        "key": part.key,
                        "records": part.records,
                        "format": part.format,
                    }
                    for part in result.parts
                ],
                "source_files": source_files,
                "skipped_files": result.skipped_files,
            },
            result.manifest_key,
        )

    def _part_key(
        self,
        output_prefix: str,
        name: str,
        run_id: str,
        suffix: str,
    ) -> str:
        return _p(
            output_prefix,
            f"name={_safe_segment(name)}",
            f"run_id={_safe_segment(run_id)}",
            f"{uuid4().hex}.{suffix}",
        )

    def _manifest_key(
        self,
        manifest_prefix: str,
        name: str,
        run_id: str,
    ) -> str:
        return _p(
            manifest_prefix,
            f"name={_safe_segment(name)}",
            f"run_id={_safe_segment(run_id)}.json",
        )

    def _new_run_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
