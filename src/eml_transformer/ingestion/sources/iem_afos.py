from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests



from eml_transformer.ingestion.base import TextSource
from eml_transformer.ingestion.registry import register_source
from eml_transformer.ingestion.schema import TextRecord
from eml_transformer.utils.stamping import stable_hash
from eml_transformer.utils.dates import parse_issued_at

import logging
logger = logging.getLogger(__name__)

@register_source("iem_afos")
class IEMAFOSSource(TextSource):
    """
    Ingest archived NWS text products from the Iowa Environmental Mesonet
    AFOS archive.

    This source can ingest one specific PIL, such as AFDIND, or many PILs
    formed from product_types x WFOs.

    Examples:
        AFDIND, HWOIND, NPWIND, LSRIND, WSWIND
        AFDLOT, HWOLOT, NPWLOT, LSRLOT, WSWLOT


    Reference: https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?help 

    Most text follows this format

        .KEY MESSAGES...
        summary bullets

        &&

        .SHORT TERM (...)
        short range forecast

        &&

        .LONG TERM (...)
        extended forecast

        &&

        .AVIATION (...)
        aviation impacts

        &&

        .WATCHES/WARNINGS/ADVISORIES...
        active alerts

        &&

        $$
        FORECASTER NAMES
    """

    name = "iem_afos"
    source_type = "api"
    update_mode = "incremental"
    supports_backfill = True
    default_lookback_days = 3

    # Weather Forecast OFfices
    DEFAULT_MISO_WFOS = [
        "IND",  # Indianapolis
        "IWX",  # Northern Indiana
        "LOT",  # Chicago
        "ILX",  # Central Illinois
        "LSX",  # St. Louis
        "DVN",  # Quad Cities
        "DMX",  # Des Moines
        "ARX",  # La Crosse
        "MKX",  # Milwaukee
        "GRR",  # Grand Rapids
        "DTX",  # Detroit
        "APX",  # Gaylord
        "MPX",  # Twin Cities
        "DLH",  # Duluth
        "PAH",  # Paducah
        "LMK",  # Louisville
        "MEG",  # Memphis
        "LZK",  # Little Rock
        "JAN",  # Jackson MS
        "LIX",  # New Orleans / Baton Rouge
        "MOB",  # Mobile
        "BMX",  # Birmingham
    ]

    #types of alerts
    DEFAULT_PRODUCT_TYPES = [
        "AFD",  # Area Forecast Discussion
        "HWO",  # Hazardous Weather Outlook
        "NPW",  # Non-precipitation warnings: heat, cold, wind, fog
        "WSW",  # Winter storm watches/warnings/advisories
        "LSR",  # Local storm reports
        "SPS",  # Special weather statements
    ]

    HEADER_RE = re.compile(
        r"""
        (?P<seq>\d{3})\s+
        (?P<wmo>[A-Z]{4}\d{2})\s+
        (?P<office>[A-Z]{4})\s+
        (?P<ddhhmm>\d{6})\s+
        (?P<pil>[A-Z]{6})
        """,
        re.VERBOSE,
    )

    SECTION_RE = re.compile(
        r"(?ms)^"
        r"\.(?P<section>[A-Z0-9 /-]+?)"
        r"(?:\s*\((?P<section_detail>.*?)\))?"
        r"\.\.\."
        r"(?P<content>.*?)(?=\n&&|\n\.[A-Z0-9 /-]+(?:\s*\(.*?\))?\.\.\.|\n\$\$|\Z)"
    )

    def __init__(
        self,
        pil: str | None = None,
        wfos: list[str] | None = None,
        product_types: list[str] | None = None,
        limit: int = 9999,
        fmt: str = "text",
        timeout: int = 30,
    ):
        self.pil = pil.upper() if pil else None
        self.wfos = [wfo.upper() for wfo in (wfos or self.DEFAULT_MISO_WFOS)]
        self.product_types = [
            product_type.upper()
            for product_type in (product_types or self.DEFAULT_PRODUCT_TYPES)
        ]

        self.limit = limit
        self.fmt = fmt
        self.timeout = timeout
        self.base_url = (
            "https://mesonet.agron.iastate.edu/"
            "cgi-bin/afos/retrieve.py"
        )

    def fetch_records(
        self,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """
        Public ingestion method.

        Returns source-native AFOS records ready to write to bronze.
        The ingestion pipeline should call this method only.
        """
        raw_responses = self._fetch_raw(
            from_date=from_date,
            to_date=to_date,
        )

        return self._parse_records(raw_responses)

    def standardize_record(
        self,
        record: dict[str, Any],
    ) -> TextRecord:
        """
        Convert one bronze/source-native AFOS record into the common TextRecord schema.
        """
        pil = record["pil"]
        raw_text = record["raw_text"]

        header = record.get("header") or self._parse_header(raw_text)
        sections = self._parse_sections(raw_text)

        issued_at_text = record.get("issued_at_text")
        published_at = record.get("published_at")

        if not published_at:
            issued_at_text, published_at = self._parse_published_at(
                raw_text=raw_text,
                pil=pil,
            )

        product_type = pil[:3]
        office = self._resolve_office(
            pil=pil,
            header=header,
        )

        key_messages = sections.get("KEY MESSAGES")
        short_term = sections.get("SHORT TERM")

        text = self._build_text(
            key_messages=key_messages,
            short_term=short_term,
            raw_text=raw_text,
        )

        record_id = stable_hash(
            {
                "source": self.name,
                "pil": pil,
                "office": office,
                "issued_code": header.get("issued_code"),
                "raw_id": header.get("raw_id"),
                "published_at": published_at,
            }
        )

        return TextRecord(
            record_id=record_id,
            source=self.name,
            source_type=self.source_type,
            title=self._make_title(
                product_type=product_type,
                office=office,
                issued_at_text=issued_at_text,
            ),
            text=text,
            published_at=published_at,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            url=self.base_url,
            region=office[-3:],
            categories=[
                "weather",
                "nws",
                "iem",
                "afos",
                product_type.lower(),
            ],
            metadata={
                "pil": pil,
                "product_type": product_type,
                "office": office,
                "sections": sections,
                "key_messages": key_messages,
                "issued_at_text": issued_at_text,
                "published_at_standardized": published_at,
                **header,
            },
            raw=raw_text,
        )

    def get_checkpoint_value(
        self,
        record: dict[str, Any],
    ) -> datetime | None:
        published_at = record.get("published_at")

        if not published_at:
            return None

        try:
            dt = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
        except Exception as e:
            raise ValueError(
                f"Malformed checkpoint datetime: {published_at!r}"
            ) from e

        if dt.tzinfo is None:
            raise ValueError(
                f"Naive checkpoint datetime: {published_at!r}"
            )

        return dt.astimezone(timezone.utc)

    def _fetch_raw(
        self,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """
        AFOS-specific download helper.

        Not called by the pipeline directly.
        """
        responses: list[dict[str, Any]] = []

        for pil in self._pils_to_fetch():
            response = requests.get(
                self.base_url,
                params={
                    "pil": pil,
                    "sdate": from_date,
                    "edate": to_date,
                    "limit": self.limit,
                    "fmt": self.fmt,
                },
                timeout=self.timeout,
            )

            response.raise_for_status()

            text = response.text.strip()

            if text and not text.startswith("ERROR:"):
                responses.append(
                    {
                        "pil": pil,
                        "response": text,
                    }
                )

        return responses

    def _parse_records(
        self,
        raw_responses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        AFOS-specific parse helper.

        Converts raw AFOS response text into source-native records for bronze.
        """
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in raw_responses:
            pil = item["pil"]
            text = item["response"]

            for chunk in self._split_products(text):
                header = self._parse_header(chunk)
                parsed_pil = header.get("pil") or pil

                try:
                    issued_at_text, published_at = self._parse_published_at(
                        raw_text=chunk,
                        pil=parsed_pil,
                    )
                except Exception:
                    logger.warning(
                        "Skipping malformed AFOS record during parse | pil=%s",
                        parsed_pil,
                        exc_info=True,
                    )
                    continue

                source_id = self._make_source_record_id(
                    pil=parsed_pil,
                    header=header,
                    published_at=published_at,
                )

                if source_id in seen_ids:
                    continue

                seen_ids.add(source_id)

                records.append(
                    {
                        "source_id": source_id,
                        "pil": parsed_pil,
                        "raw_text": chunk,
                        "header": header,
                        "issued_at_text": issued_at_text,
                        "published_at": published_at,
                    }
                )

        return records

    def _make_source_record_id(
        self,
        pil: str,
        header: dict[str, str | None],
        published_at: str,
    ) -> str:
        return stable_hash(
            {
                "pil": pil,
                "office": header.get("office"),
                "issued_code": header.get("issued_code"),
                "raw_id": header.get("raw_id"),
                "published_at": published_at,
            }
        )

    def _parse_published_at(
        self,
        raw_text: str,
        pil: str,
    ) -> tuple[str, str]:
        issued_at_text = self._extract_issued_text(raw_text)

        try:
            published_at = parse_issued_at(issued_at_text)
        except Exception as e:
            raise ValueError(
                f"Failed to parse published_at for PIL={pil}: "
                f"{issued_at_text!r}"
            ) from e

        if not published_at:
            raise ValueError(
                f"Missing published_at for PIL={pil}: {issued_at_text!r}"
            )

        if not isinstance(published_at, str):
            raise TypeError(
                f"published_at must be ISO datetime string, "
                f"got {type(published_at)}"
            )

        try:
            parsed_dt = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
        except Exception as e:
            raise ValueError(
                f"Malformed ISO datetime published_at for PIL={pil}: "
                f"{published_at!r}"
            ) from e

        if parsed_dt.tzinfo is None:
            raise ValueError(
                f"Naive datetime published_at for PIL={pil}: "
                f"{published_at!r}"
            )

        return issued_at_text or "", parsed_dt.astimezone(timezone.utc).isoformat()

    def _pils_to_fetch(self) -> list[str]:
        if self.pil:
            return [self.pil]

        return [
            f"{product_type}{wfo}"
            for product_type in self.product_types
            for wfo in self.wfos
        ]

    def _split_products(
        self,
        raw: str,
    ) -> list[str]:
        text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        matches = list(self.HEADER_RE.finditer(text))

        records = []

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            records.append(text[start:end].strip())

        return records

    def _parse_header(
        self,
        text: str,
    ) -> dict[str, str | None]:
        match = self.HEADER_RE.search(text)

        if not match:
            return {
                "raw_id": None,
                "wmo": None,
                "wmo_header": None,
                "office": None,
                "issued_code": None,
                "pil": None,
            }

        wmo_header = (
            f"{match.group('wmo')} "
            f"{match.group('office')} "
            f"{match.group('ddhhmm')}"
        )

        return {
            "raw_id": match.group("seq"),
            "wmo": match.group("wmo"),
            "wmo_header": wmo_header,
            "office": match.group("office"),
            "issued_code": match.group("ddhhmm"),
            "pil": match.group("pil"),
        }

    def _parse_sections(
        self,
        text: str,
    ) -> dict[str, str]:
        sections: dict[str, str] = {}

        for match in self.SECTION_RE.finditer(text):
            section = match.group("section").strip()
            section = re.sub(r"\s+", " ", section)

            sections[section] = match.group("content").strip()

        return sections

    def _extract_issued_text(
        self,
        text: str,
    ) -> str | None:
        match = re.search(
            r"(?m)^Issued at .+$",
            text,
        )

        if match:
            return match.group(0).strip()

        match = re.search(
            r"(?m)^National Weather Service .*\n(.+)$",
            text,
        )

        if match:
            return match.group(1).strip()

        return None

    def _resolve_office(
        self,
        pil: str,
        header: dict[str, str | None],
    ) -> str:
        office = header.get("office")

        if not office:
            office = pil[3:] if len(pil) >= 6 else None

        if not office:
            raise ValueError(f"Could not determine office for PIL={pil}")

        return office

    def _build_text(
        self,
        key_messages: str | None,
        short_term: str | None,
        raw_text: str,
    ) -> str:
        text = "\n\n".join(
            part.strip()
            for part in [key_messages, short_term]
            if isinstance(part, str) and part.strip()
        )

        return text if text else raw_text.strip()

    def _make_title(
        self,
        product_type: str,
        office: str | None,
        issued_at_text: str | None,
    ) -> str:
        parts = [
            product_type,
            office or "",
            issued_at_text or "",
        ]

        return " | ".join(part for part in parts if part)