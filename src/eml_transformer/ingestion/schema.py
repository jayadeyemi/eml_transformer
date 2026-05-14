from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class TextRecord:
    record_id: str
    source: str
    source_type: str

    title: str | None
    text: str

    published_at: datetime | None
    retrieved_at: datetime

    url: str | None = None
    region: str | None = None
    categories: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TEXT_RECORD_COLUMNS = [
    "record_id",
    "source",
    "source_type",
    "title",
    "text",
    "published_at",
    "retrieved_at",
    "url",
    "region",
    "categories",
    "raw",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)