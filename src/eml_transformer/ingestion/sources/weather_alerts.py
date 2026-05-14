from typing import Any

import requests

from eml_transformer.ingestion.base import TextSource
from eml_transformer.ingestion.registry import register_source
from eml_transformer.ingestion.schema import TextRecord, utc_now


MISO_AREAS = [
    "IN", "IL", "MI", "OH", "KY",
    "WI", "MN", "IA", "MO", "AR",
    "LA", "MS", "ND", "SD",
]


@register_source("weather_alerts")
class WeatherAlertSource(TextSource):
    name = "weather_alerts"
    source_type = "api"

    def __init__(
        self,
        areas: list[str] | str | None = None,
        timeout: int = 30,
    ):
        if areas is None:
            areas = MISO_AREAS

        if isinstance(areas, str):
            areas = [areas]

        self.areas = areas
        self.timeout = timeout
        self.base_url = "https://api.weather.gov/alerts/active"

        self.headers = {
            "User-Agent": "eml-transformer-research jackyeung99@gmail.com",
            "Accept": "application/geo+json",
        }

    def fetch_raw(self) -> list[dict[str, Any]]:
        responses = []

        for area in self.areas:
            response = requests.get(
                self.base_url,
                params={"area": area},
                headers=self.headers,
                timeout=self.timeout,
            )

            response.raise_for_status()

            responses.append({
                "query_area": area,
                "response": response.json(),
            })

        return responses

    def parse_records(
        self,
        raw: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records = []
        seen_ids = set()

        for item in raw:
            query_area = item["query_area"]
            features = item["response"].get("features", [])

            for feature in features:
                props = feature.get("properties", {})
                source_id = props.get("id") or feature.get("id")

                if source_id in seen_ids:
                    continue

                seen_ids.add(source_id)

                records.append({
                    "query_area": query_area,
                    "feature": feature,
                })

        return records

    def standardize_record(self, record: dict[str, Any]) -> TextRecord:
        query_area = record.get("query_area")
        feature = record.get("feature", {})
        props = feature.get("properties", {})

        headline = props.get("headline")
        description = props.get("description")
        instruction = props.get("instruction")
        event = props.get("event")

        text = "\n".join(
            part for part in [
                headline,
                description,
                instruction,
            ]
            if part
        )

        source_id = props.get("id") or feature.get("id")

        return TextRecord(
            record_id=self._make_record_id(
                self.name,
                source_id,
                props.get("sent"),
                headline,
            ),
            source=self.name,
            source_type=self.source_type,
            title=headline,
            text=text,
            published_at=props.get("sent"),
            retrieved_at=utc_now(),
            url=props.get("@id"),
            region=query_area,
            categories=[
                event,
                props.get("severity"),
                props.get("urgency"),
            ],
            metadata={
                "source_id": source_id,
                "query_area": query_area,
                "event": event,
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "certainty": props.get("certainty"),
                "status": props.get("status"),
                "message_type": props.get("messageType"),
                "category": props.get("category"),
                "response": props.get("response"),
                "sender": props.get("sender"),
                "sender_name": props.get("senderName"),
                "area_desc": props.get("areaDesc"),
                "geocode": props.get("geocode"),
                "affected_zones": props.get("affectedZones"),
                "effective_at": props.get("effective"),
                "expires_at": props.get("expires"),
                "ends_at": props.get("ends"),
            },
            raw=feature,
        )