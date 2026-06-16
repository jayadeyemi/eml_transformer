from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import requests

from eml_transformer.storage.storage import Storage


GKG_COLUMNS = [
    "GKGRECORDID",
    "DATE",
    "SourceCollectionIdentifier",
    "SourceCommonName",
    "DocumentIdentifier",
    "Counts",
    "V2Counts",
    "Themes",
    "V2Themes",
    "Locations",
    "V2Locations",
    "Persons",
    "V2Persons",
    "Organizations",
    "V2Organizations",
    "Tone",
    "Dates",
    "GCAM",
    "SharingImage",
    "RelatedImages",
    "SocialImageEmbeds",
    "SocialVideoEmbeds",
    "Quotations",
    "AllNames",
    "Amounts",
    "TranslationInfo",
    "Extras",
]

CORE_THEMES = {
    "NATURAL_DISASTER_EXTREME_WEATHER",
    "NATURAL_DISASTER_SEVERE_WEATHER",
    "NATURAL_DISASTER_FLOODING",
    "NATURAL_DISASTER_HURRICANE",
    "NATURAL_DISASTER_TORNADO",
    "NATURAL_DISASTER_WILDFIRE",
    "POWER_OUTAGE",
    "MANMADE_DISASTER_POWER_OUTAGE",
    "MANMADE_DISASTER_POWER_OUTAGES",
    "MANMADE_DISASTER_WITHOUT_POWER",
    "MANMADE_DISASTER_WITHOUT_ELECTRICITY",
}

URL_KEYWORDS = {
    "storm",
    "flood",
    "flooding",
    "hurricane",
    "tornado",
    "wildfire",
    "blizzard",
    "outage",
    "blackout",
    "power-outage",
    "power_outage",
    "without-power",
}

BAD_DOMAINS = {
    "slashfilm.com",
    "screenrant.com",
    "collider.com",
    "people.com",
    "tmz.com",
    "variety.com",
    "hollywoodreporter.com",
    "deadline.com",
    "ew.com",
    "thewrap.com",
    "cinemablend.com",
    "comicbook.com",
}

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
DEFAULT_PARSER_VERSION = "gdelt_gkg_v1"
DEFAULT_FILTER_VERSION = "weather_outage_us_v1"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GdeltDiscoveryResult:
    run_id: str
    date: str
    raw_rows: int
    filtered_rows: int
    urls: list[dict[str, Any]]
    failures: list[dict[str, str]]


@dataclass(frozen=True)
class GdeltFileDiscoveryResult:
    run_id: str
    date: str
    timestamp: str
    source_url: str
    raw_key: str
    candidate_urls_key: str
    manifest_key: str
    raw_rows: int
    filtered_rows: int
    urls: list[dict[str, Any]]
    downloaded: bool
    parsed_from_cache: bool
    raw_content_hash: str | None = None
    raw_size_bytes: int | None = None
    error: str | None = None


def timestamps_for_day(date: str) -> list[str]:
    date = resolve_gdelt_date(date)
    start = datetime.strptime(date, "%Y-%m-%d")
    return [
        (start + timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        for i in range(96)
    ]


def resolve_gdelt_date(date: str) -> str:
    normalized = date.strip().lower()

    if normalized == "today":
        return datetime.utcnow().date().isoformat()

    if normalized == "yesterday":
        return (datetime.utcnow().date() - timedelta(days=1)).isoformat()

    datetime.strptime(date, "%Y-%m-%d")
    return date


def timestamp_to_gkg_url(timestamp: str) -> str:
    return f"http://data.gdeltproject.org/gdeltv2/{timestamp}.gkg.csv.zip"


def date_from_timestamp(timestamp: str) -> str:
    return datetime.strptime(timestamp[:8], "%Y%m%d").date().isoformat()


def gdelt_raw_key(timestamp: str, table: str = "gkg") -> str:
    date = date_from_timestamp(timestamp)
    return (
        "bronze/gdelt/raw/"
        f"table={table}/"
        f"date={date}/"
        f"timestamp={timestamp}/"
        f"{timestamp}.{table}.csv.zip"
    )


def gdelt_candidate_urls_key(
    timestamp: str,
    parser_version: str = DEFAULT_PARSER_VERSION,
    filter_version: str = DEFAULT_FILTER_VERSION,
    table: str = "gkg",
) -> str:
    date = date_from_timestamp(timestamp)
    return (
        "bronze/gdelt/candidate_urls/"
        f"table={table}/"
        f"date={date}/"
        f"timestamp={timestamp}/"
        f"parser_version={parser_version}/"
        f"filter_version={filter_version}/"
        "candidate_urls.jsonl"
    )


def gdelt_file_manifest_key(
    timestamp: str,
    parser_version: str = DEFAULT_PARSER_VERSION,
    filter_version: str = DEFAULT_FILTER_VERSION,
    table: str = "gkg",
) -> str:
    date = date_from_timestamp(timestamp)
    return (
        "manifests/gdelt_files/"
        f"table={table}/"
        f"date={date}/"
        f"timestamp={timestamp}/"
        f"parser_version={parser_version}/"
        f"filter_version={filter_version}.json"
    )


def load_gkg_file(
    timestamp: str,
    timeout: int = 60,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    http = session or requests.Session()
    raw_bytes = download_gkg_file(timestamp=timestamp, timeout=timeout, session=http)
    return load_gkg_bytes(raw_bytes=raw_bytes, timestamp=timestamp)


def download_gkg_file(
    timestamp: str,
    timeout: int = 60,
    session: requests.Session | None = None,
) -> bytes:
    http = session or requests.Session()
    response = http.get(timestamp_to_gkg_url(timestamp), timeout=timeout)
    response.raise_for_status()
    return response.content


def load_gkg_bytes(raw_bytes: bytes, timestamp: str) -> pd.DataFrame:

    with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
        filename = archive.namelist()[0]
        df = pd.read_csv(
            archive.open(filename),
            sep="\t",
            header=None,
            dtype=str,
            low_memory=False,
        )

    df.columns = GKG_COLUMNS[: len(df.columns)]
    df["gdelt_timestamp"] = timestamp
    df["gdelt_source_url"] = timestamp_to_gkg_url(timestamp)
    return df


def parse_themes(value: str) -> set[str]:
    if pd.isna(value):
        return set()

    return {theme.strip().upper() for theme in value.split(";") if theme.strip()}


def is_us_location(value: str) -> bool:
    if pd.isna(value):
        return False

    return any("#US#" in part or "United States" in part for part in value.split(";"))


def url_has_keyword(value: str) -> bool:
    if pd.isna(value):
        return False

    value = value.lower()
    return any(keyword in value for keyword in URL_KEYWORDS)


def clean_domain(value: str) -> str:
    if pd.isna(value) or not value:
        return ""

    domain = urlparse(str(value)).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def canonicalize_url(value: str) -> str:
    parsed = urlparse(str(value).strip())

    if not parsed.scheme or not parsed.netloc:
        return str(value).strip()

    query_pairs = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()

        if key_lower in TRACKING_QUERY_KEYS:
            continue

        if any(key_lower.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue

        query_pairs.append((key, val))

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower().removeprefix("www."),
        path=path,
        params="",
        query=urlencode(query_pairs, doseq=True),
        fragment="",
    )
    return urlunparse(normalized)


def url_hash(value: str) -> str:
    return hashlib.sha256(canonicalize_url(value).encode("utf-8")).hexdigest()


def filter_gkg(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["_theme_set"] = df["Themes"].apply(parse_themes)
    df["_matched_themes"] = df["_theme_set"].apply(lambda s: sorted(s & CORE_THEMES))
    df["_theme_count"] = df["_matched_themes"].apply(len)
    df["_domain"] = df["DocumentIdentifier"].apply(clean_domain)

    theme_mask = df["_theme_count"] >= 1
    us_mask = df["Locations"].apply(is_us_location)
    url_mask = df["DocumentIdentifier"].apply(url_has_keyword)
    domain_mask = ~df["_domain"].isin(BAD_DOMAINS)

    return df[theme_mask & us_mask & url_mask & domain_mask].copy()


def dataframe_to_url_records(df: pd.DataFrame, run_id: str) -> list[dict[str, Any]]:
    if df.empty:
        return []

    rows = []
    keep_cols = [
        "DATE",
        "GKGRECORDID",
        "SourceCommonName",
        "DocumentIdentifier",
        "_domain",
        "_matched_themes",
        "Themes",
        "Locations",
        "Tone",
        "gdelt_timestamp",
        "gdelt_source_url",
    ]

    for record in df.reindex(columns=keep_cols).to_dict(orient="records"):
        canonical_url = canonicalize_url(record["DocumentIdentifier"])
        rows.append(
            {
                "run_id": run_id,
                "source": "gdelt",
                "canonical_url": canonical_url,
                "url_hash": url_hash(canonical_url),
                "source_url": record["DocumentIdentifier"],
                "source_domain": record["_domain"],
                "gdelt_record_id": record.get("GKGRECORDID"),
                "gdelt_timestamp": record.get("gdelt_timestamp"),
                "gdelt_source_url": record.get("gdelt_source_url"),
                "published_at": record.get("DATE"),
                "matched_themes": record.get("_matched_themes") or [],
                "raw": record,
            }
        )

    seen = set()
    deduped = []

    for row in rows:
        if row["url_hash"] in seen:
            continue

        seen.add(row["url_hash"])
        deduped.append(row)

    return deduped


def discover_gdelt_file(
    timestamp: str,
    run_id: str,
    storage: Storage,
    timeout: int = 60,
    parser_version: str = DEFAULT_PARSER_VERSION,
    filter_version: str = DEFAULT_FILTER_VERSION,
    use_cache: bool = True,
) -> GdeltFileDiscoveryResult:
    date = date_from_timestamp(timestamp)
    raw_key = gdelt_raw_key(timestamp)
    candidate_key = gdelt_candidate_urls_key(
        timestamp=timestamp,
        parser_version=parser_version,
        filter_version=filter_version,
    )
    manifest_key = gdelt_file_manifest_key(
        timestamp=timestamp,
        parser_version=parser_version,
        filter_version=filter_version,
    )
    downloaded = False
    parsed_from_cache = False
    manifest: dict[str, Any] = {}

    if use_cache and storage.exists(manifest_key):
        manifest = storage.read_json(manifest_key)

    if use_cache and storage.exists(candidate_key):
        urls = [
            {**row, "run_id": run_id}
            for row in storage.read_jsonl(candidate_key)
        ]
        return GdeltFileDiscoveryResult(
            run_id=run_id,
            date=date,
            timestamp=timestamp,
            source_url=timestamp_to_gkg_url(timestamp),
            raw_key=raw_key,
            candidate_urls_key=candidate_key,
            manifest_key=manifest_key,
            raw_rows=int(manifest.get("raw_rows", 0)),
            filtered_rows=int(manifest.get("filtered_rows", len(urls))),
            urls=urls,
            downloaded=False,
            parsed_from_cache=True,
            raw_content_hash=manifest.get("raw_content_hash"),
            raw_size_bytes=manifest.get("raw_size_bytes"),
        )

    if use_cache and storage.exists(raw_key):
        raw_bytes = storage.read_bytes(raw_key)
        raw_content_hash = manifest.get("raw_content_hash")
    else:
        raw_bytes = download_gkg_file(timestamp=timestamp, timeout=timeout)
        storage.write_bytes(raw_bytes, raw_key)
        downloaded = True
        raw_content_hash = hashlib.sha256(raw_bytes).hexdigest()

    if raw_content_hash is None:
        raw_content_hash = hashlib.sha256(raw_bytes).hexdigest()

    df = load_gkg_bytes(raw_bytes=raw_bytes, timestamp=timestamp)
    raw_rows = len(df)
    filtered = filter_gkg(df)
    urls = dataframe_to_url_records(filtered, run_id=run_id)
    storage_urls = [
        {k: v for k, v in row.items() if k != "run_id"}
        for row in urls
    ]
    filtered_rows = len(filtered)

    storage.write_jsonl(candidate_key, storage_urls)
    storage.write_json(
        {
            "source": "gdelt",
            "table": "gkg",
            "timestamp": timestamp,
            "date": date,
            "source_url": timestamp_to_gkg_url(timestamp),
            "raw_key": raw_key,
            "candidate_urls_key": candidate_key,
            "raw_content_hash": raw_content_hash,
            "raw_size_bytes": len(raw_bytes),
            "raw_rows": raw_rows,
            "filtered_rows": filtered_rows,
            "urls_discovered": len(urls),
            "parser_version": parser_version,
            "filter_version": filter_version,
            "downloaded_at": utc_timestamp()
            if downloaded
            else manifest.get("downloaded_at"),
            "processed_at": utc_timestamp(),
            "status": "parsed",
        },
        manifest_key,
    )

    return GdeltFileDiscoveryResult(
        run_id=run_id,
        date=date,
        timestamp=timestamp,
        source_url=timestamp_to_gkg_url(timestamp),
        raw_key=raw_key,
        candidate_urls_key=candidate_key,
        manifest_key=manifest_key,
        raw_rows=raw_rows,
        filtered_rows=filtered_rows,
        urls=urls,
        downloaded=downloaded,
        parsed_from_cache=parsed_from_cache,
        raw_content_hash=raw_content_hash,
        raw_size_bytes=len(raw_bytes),
    )


def iter_gdelt_file_discoveries(
    date: str,
    run_id: str,
    storage: Storage,
    max_files: int | None = None,
    timeout: int = 60,
    parser_version: str = DEFAULT_PARSER_VERSION,
    filter_version: str = DEFAULT_FILTER_VERSION,
    use_cache: bool = True,
) -> Iterator[GdeltFileDiscoveryResult]:
    date = resolve_gdelt_date(date)

    for timestamp in timestamps_for_day(date)[:max_files]:
        try:
            yield discover_gdelt_file(
                timestamp=timestamp,
                run_id=run_id,
                storage=storage,
                timeout=timeout,
                parser_version=parser_version,
                filter_version=filter_version,
                use_cache=use_cache,
            )
        except Exception as exc:
            yield GdeltFileDiscoveryResult(
                run_id=run_id,
                date=date,
                timestamp=timestamp,
                source_url=timestamp_to_gkg_url(timestamp),
                raw_key=gdelt_raw_key(timestamp),
                candidate_urls_key=gdelt_candidate_urls_key(
                    timestamp=timestamp,
                    parser_version=parser_version,
                    filter_version=filter_version,
                ),
                manifest_key=gdelt_file_manifest_key(
                    timestamp=timestamp,
                    parser_version=parser_version,
                    filter_version=filter_version,
                ),
                raw_rows=0,
                filtered_rows=0,
                urls=[],
                downloaded=False,
                parsed_from_cache=False,
                error=str(exc),
            )


def discover_gdelt_articles(
    date: str,
    run_id: str,
    max_files: int | None = None,
    timeout: int = 60,
) -> GdeltDiscoveryResult:
    date = resolve_gdelt_date(date)
    raw_rows = 0
    filtered_rows = 0
    urls: list[dict[str, Any]] = []
    seen_url_hashes: set[str] = set()
    failures = []

    for timestamp in timestamps_for_day(date)[:max_files]:
        try:
            df = load_gkg_file(timestamp=timestamp, timeout=timeout)
        except Exception as exc:
            failures.append({"timestamp": timestamp, "error": str(exc)})
            continue

        raw_rows += len(df)
        filtered = filter_gkg(df)

        if not filtered.empty:
            filtered_rows += len(filtered)

            for row in dataframe_to_url_records(filtered, run_id=run_id):
                if row["url_hash"] in seen_url_hashes:
                    continue

                seen_url_hashes.add(row["url_hash"])
                urls.append(row)

    return GdeltDiscoveryResult(
        run_id=run_id,
        date=date,
        raw_rows=raw_rows,
        filtered_rows=filtered_rows,
        urls=urls,
        failures=failures,
    )
