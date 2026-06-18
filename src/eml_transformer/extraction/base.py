from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExtractedArticle:
    url: str
    title: str | None
    text: str | None
    success: bool
    error: str | None = None


class ArticleExtractor(ABC):

    @abstractmethod
    def extract(self, url: str) -> ExtractedArticle:
        pass