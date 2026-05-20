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

    Supports normal incremental runs and date-windowed backfills.
    """

    name = "newsapi"
    source_type = "api"
    update_mode = "incremental"

    def __init__(
        self,
        api_key: str,
        query: str,
        language: str = "en",
        sort_by: str = "relevancy",
        page_size: int = 100,
        max_pages: int = 1,
        from_date: str | None = None,
        to_date: str | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.query = query
        self.language = language
        self.sort_by = sort_by
        self.page_size = page_size
        self.max_pages = max_pages
        self.from_date = from_date
        self.to_date = to_date
        self.timeout = timeout

        self.base_url = "https://newsapi.org/v2/everything"

        self.headers = {
            "User-Agent": "eml-transformer-research",
        }

    def fetch_page(
        self,
        page: int,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch one page from NewsAPI.
        """

        params = {
            "q": self.query,
            "language": self.language,
            "sortBy": self.sort_by,
            "pageSize": self.page_size,
            "page": page,
            "apiKey": self.api_key,
        }

        effective_from_date = from_date or self.from_date
        effective_to_date = to_date or self.to_date

        if effective_from_date:
            params["from"] = effective_from_date

        if effective_to_date:
            params["to"] = effective_to_date

        response = requests.get(
            self.base_url,
            params=params,
            headers=self.headers,
            timeout=self.timeout,
        )

        response.raise_for_status()
        return response.json()

    def fetch_raw(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch all configured pages for either:
        - normal incremental ingestion
        - explicit date-window backfills
        """

        all_articles: list[dict[str, Any]] = []
        total_results: int | None = None

        effective_from_date = from_date or self.from_date
        effective_to_date = to_date or self.to_date

        for page in range(1, self.max_pages + 1):
            raw = self.fetch_page(
                page=page,
                from_date=effective_from_date,
                to_date=effective_to_date,
            )

            if raw.get("status") != "ok":
                raise RuntimeError(
                    f"NewsAPI request failed: {raw}"
                )

            if total_results is None:
                total_results = raw.get("totalResults")

            articles = raw.get("articles", [])

            if not articles:
                break

            all_articles.extend(articles)

            if len(articles) < self.page_size:
                break

            if total_results and len(all_articles) >= total_results:
                break

        return {
            "status": "ok",
            "totalResults": total_results or len(all_articles),
            "articles": all_articles,
            "query": self.query,
            "from": effective_from_date,
            "to": effective_to_date,
        }

    def parse_records(self, raw: Any) -> list[dict[str, Any]]:
        """
        Extract article records from the raw NewsAPI response.
        """

        return raw.get("articles", [])

    def standardize_record(
        self,
        article: dict[str, Any],
    ) -> TextRecord:
        source_info = article.get("source") or {}

        title = article.get("title")
        description = article.get("description")
        content = article.get("content")
        published_at = article.get("publishedAt")
        url = article.get("url")

        text = "\n".join(
            part
            for part in [title, description, content]
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
    
    def get_checkpoint_value(
        self,
        raw_record: dict[str, Any],
    ) -> str | None:
        return raw_record.get("publishedAt")