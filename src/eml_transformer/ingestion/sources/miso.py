from typing import Any

import requests
from bs4 import BeautifulSoup

from eml_transformer.ingestion.base import TextSource
from eml_transformer.ingestion.registry import register_source
from eml_transformer.ingestion.schema import TextRecord, utc_now


@register_source("miso_notifications")
class MISONotificationSource(TextSource):
    name = "miso_notifications"
    source_type = "api"
    update_mode = "snapshot"

    def __init__(
        self,
        topic: str = "",
        take: int = 0,
        base_url: str = "https://www.misoenergy.org/api/topicnotifications/GetGroupedNotifications",
        timeout: int = 30,
    ):
        self.topic = topic
        self.take = take
        self.base_url = base_url
        self.timeout = timeout

        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.misoenergy.org/markets-and-operations/notifications/",
        }

    def fetch_raw(self) -> Any:
        params = {
            "topic": self.topic,
            "take": self.take,
        }

        response = requests.get(
            self.base_url,
            params=params,
            headers=self.headers,
            timeout=self.timeout,
        )

        response.raise_for_status()
        return response.json()

    def parse_records(self, raw: Any) -> list[dict[str, Any]]:
        records = []

        for group in raw:
            topic = group.get("topic")
            notifications = group.get("notifications", [])

            for notification in notifications:
                records.append({
                    "topic": topic,
                    "notification": notification,
                })

        return records

    def standardize_record(self, record: dict[str, Any]) -> TextRecord:
        topic = record.get("topic")
        notification = record.get("notification", {})

        subject = notification.get("subject")
        publish_date = notification.get("publishDate")
        body_html = notification.get("body") or ""

        body_text = BeautifulSoup(
            body_html,
            "html.parser",
        ).get_text(" ", strip=True)

        url = self._build_url(notification)

        return TextRecord(
            record_id=self._make_record_id(
                self.name,
                notification.get("id"),
                url,
                publish_date,
                subject,
            ),
            source=self.name,
            source_type=self.source_type,
            title=subject,
            text=body_text,
            published_at=publish_date,
            retrieved_at=utc_now(),
            url=url,
            region="MISO",
            categories=[
                "market_notice",
                topic,
            ],
            metadata={
                "topic": topic,
                "notification_id": notification.get("id"),
                "publish_date": publish_date,
            },
            raw=notification,
        )

    def _build_url(self, notification: dict[str, Any]) -> str | None:
        link = notification.get("permanentLinkUrl")

        if not link:
            return None

        if link.startswith("http"):
            return link

        return f"https://www.misoenergy.org{link}"