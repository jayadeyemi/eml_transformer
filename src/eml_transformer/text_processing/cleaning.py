'''
set of helper functions to clean textual data

'''


import re
import unicodedata

from bs4 import BeautifulSoup


def strip_html(text: str) -> str:
    return BeautifulSoup(text, "html.parser").get_text(
        separator=" "
    )


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def remove_empty_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    return "\n".join(lines)

# def remove_boilerplate(text: str) -> str:
#     boilerplate_patterns = [
#         r"",
#     ]

#     for pattern in boilerplate_patterns:
#         text = re.sub(
#             pattern,
#             "",
#             text,
#             flags=re.IGNORECASE,
#         )

#     return text


def truncate_text(
    text: str,
    max_chars: int = 8000,
) -> str:
    return text[:max_chars]


def clean_text(text: str) -> str:
    text = strip_html(text)
    text = normalize_unicode(text)
    text = normalize_whitespace(text)
    text = truncate_text(text)

    return text.strip()