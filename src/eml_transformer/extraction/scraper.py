from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
import trafilatura
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


@dataclass
class ArticleScraperConfig:
    request_timeout: int = 15
    playwright_timeout: int = 30_000
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
    fallback_on_forbidden: bool = True


class HybridArticleScraper:
    def __init__(self, config: ArticleScraperConfig | None = None):
        self.config = config or ArticleScraperConfig()

    async def scrape(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> dict:
        retrieved_at = datetime.now(timezone.utc).isoformat()

        fetch_result = await self._fetch_with_aiohttp(
            session=session,
            url=url,
        )

        fallback_used = False

        if (
            self.config.fallback_on_forbidden
            and not fetch_result["success"]
            and fetch_result["error_type"] == "forbidden"
        ):
            fallback_used = True
            fetch_result = await self._fetch_with_playwright(url)

        if not fetch_result["success"]:
            return self._failure_result(
                url=url,
                fetch_result=fetch_result,
                retrieved_at=retrieved_at,
                fallback_used=fallback_used,
            )

        extracted = self._extract_with_trafilatura(
            html=fetch_result["html"],
            url=url,
            extractor=f"trafilatura_after_{fetch_result['method']}",
        )

        scrape_status = "success" if extracted["success"] else "parse_failed"

        return {
            "url": url,
            "success": extracted["success"],
            "scrape_status": scrape_status,
            "status_code": fetch_result["status_code"],
            "error_type": extracted["error_type"],
            "error_message": extracted["error_message"],
            "fetch_method": fetch_result["method"],
            "fallback_used": fallback_used,
            "extractor": extracted["extractor"],
            "title": extracted["title"],
            "author": extracted["author"],
            "date": extracted["date"],
            "text": extracted["text"],
            "text_length": len(extracted["text"]),
            "retrieved_at": retrieved_at,
            "attempt_count": 2 if fallback_used else 1,
        }

    async def _fetch_with_aiohttp(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> dict:
        try:
            async with session.get(
                url,
                timeout=self.config.request_timeout,
            ) as response:
                text = await response.text(errors="ignore")

                if response.status >= 400:
                    return {
                        "success": False,
                        "html": None,
                        "status_code": response.status,
                        "error_type": self._categorize_status(response.status),
                        "error_message": f"HTTP {response.status}",
                        "method": "aiohttp",
                    }

                return {
                    "success": True,
                    "html": text,
                    "status_code": response.status,
                    "error_type": None,
                    "error_message": None,
                    "method": "aiohttp",
                }

        except TimeoutError as exc:
            return self._fetch_error("timeout", str(exc), "aiohttp")

        except aiohttp.ClientConnectionError as exc:
            return self._fetch_error("connection_error", str(exc), "aiohttp")

        except Exception as exc:
            return self._fetch_error("unknown", str(exc), "aiohttp")

    async def _fetch_with_playwright(self, url: str) -> dict:
        browser = None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)

                page = await browser.new_page(
                    user_agent=self.config.user_agent,
                )

                response = await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=self.config.playwright_timeout,
                )

                html = await page.content()
                status_code = response.status if response else None

                await browser.close()

            return {
                "success": True,
                "html": html,
                "status_code": status_code,
                "error_type": None,
                "error_message": None,
                "method": "playwright",
            }

        except PlaywrightTimeoutError as exc:
            return self._fetch_error(
                "playwright_timeout",
                str(exc),
                "playwright",
            )

        except Exception as exc:
            return self._fetch_error(
                "playwright_error",
                str(exc),
                "playwright",
            )

        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    def _extract_with_trafilatura(
        self,
        html: str,
        url: str,
        extractor: str,
    ) -> dict:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
        )

        if result is None:
            return {
                "success": False,
                "extractor": extractor,
                "title": None,
                "author": None,
                "date": None,
                "text": "",
                "error_type": "parse_failed",
                "error_message": "trafilatura returned None",
            }

        parsed = json.loads(result)
        text = parsed.get("text", "") or ""

        return {
            "success": True,
            "extractor": extractor,
            "title": parsed.get("title"),
            "author": parsed.get("author"),
            "date": parsed.get("date"),
            "text": text,
            "error_type": None,
            "error_message": None,
        }

    def _failure_result(
        self,
        url: str,
        fetch_result: dict,
        retrieved_at: str,
        fallback_used: bool,
    ) -> dict:
        return {
            "url": url,
            "success": False,
            "scrape_status": fetch_result["error_type"],
            "status_code": fetch_result["status_code"],
            "error_type": fetch_result["error_type"],
            "error_message": fetch_result["error_message"],
            "fetch_method": fetch_result["method"],
            "fallback_used": fallback_used,
            "extractor": None,
            "title": None,
            "author": None,
            "date": None,
            "text": "",
            "text_length": 0,
            "retrieved_at": retrieved_at,
            "attempt_count": 2 if fallback_used else 1,
        }

    def _fetch_error(
        self,
        error_type: str,
        error_message: str,
        method: str,
    ) -> dict:
        return {
            "success": False,
            "html": None,
            "status_code": None,
            "error_type": error_type,
            "error_message": error_message,
            "method": method,
        }

    def _categorize_status(self, status: int | None) -> str:
        if status == 403:
            return "forbidden"
        if status == 404:
            return "not_found"
        if status == 429:
            return "rate_limited"
        if status is not None and 500 <= status < 600:
            return "server_error"
        if status is not None:
            return "http_error"
        return "unknown"