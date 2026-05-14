from datetime import datetime, timezone
from typing import Any

import requests

from eml_transformer.ingestion.base import TextSource
from eml_transformer.ingestion.registry import register_source
from eml_transformer.ingestion.schema import TextRecord


@register_source("newsapi")
class NewsAPISource(TextSource):
    """
    Ingest news articles from NewsAPI.
    """

    name = "newsapi"
    source_type = "api"

    def __init__(
        self,
        api_key: str,
        query: str,
        language: str = "en",
        sort_by: str = "relevancy",
        page_size: int = 100,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.query = query
        self.language = language
        self.sort_by = sort_by
        self.page_size = page_size
        self.timeout = timeout

        self.base_url = "https://newsapi.org/v2/everything"

        self.headers = {
            "User-Agent": "Mozilla/5.0",
        }

    def fetch_raw(self) -> Any:
        params = {
            "q": self.query,
            "language": self.language,
            "pageSize": self.page_size,
            "sortBy": self.sort_by,
            "apiKey": self.api_key,
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
        """
        Extract article records from the raw NewsAPI response.
        """
        return raw.get("articles", [])

    def standardize_record(self, article: dict[str, Any]) -> TextRecord:
        source_info = article.get("source") or {}

        title = article.get("title")
        description = article.get("description")
        content = article.get("content")
        published_at = article.get("publishedAt")
        url = article.get("url")

        text = "\n".join(
            part for part in [title, description, content]
            if part
        )

        source_name = source_info.get("name")

        return TextRecord(
            record_id=self._make_record_id(
                url,
                published_at,
                title,
            ),
            source=self.name,
            source_type=self.source_type,
            title=title,
            text=text,
            published_at=published_at,
            retrieved_at=datetime.now(timezone.utc),
            url=url,
            region=None,
            categories=["news"],
            metadata={
                "news_source": source_name,
                "news_source_id": source_info.get("id"),
                "author": article.get("author"),
                "query": self.query,
                "language": self.language,
                "sort_by": self.sort_by,
            },
            raw=article,
        )