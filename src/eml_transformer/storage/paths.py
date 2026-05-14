# src/qbt/storage/paths.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional


def _clean(x: str) -> str:
    """
    Make a string safe for partition-style paths (strategy=..., etc.).
    Keep it predictable across local FS + S3.
    """
    return (
        str(x).strip()
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
        .replace("=", "-")
    )


def _p(*parts: str) -> str:
    """Join POSIX key parts safely."""
    return str(PurePosixPath(*parts))


@dataclass(frozen=True)
class StoragePaths:
    """
    Key layout for EML Transformer artifacts.

    Notes
    -----
    - All functions return *keys* (POSIX-like strings), not local filesystem Paths.
    - Storage backend maps keys -> local paths or S3 URIs.
    - Partition style: key=val folders for query-friendly datasets.
    """

    # ---------------------------------------------------------------------
    # Data construction pipeline
    # ---------------------------------------------------------------------
  