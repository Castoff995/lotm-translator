from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .text import clean_story_text


USER_AGENT = "LOTM-Translator-Research/0.1 (local personal corpus; respectful-rate-limit)"


def _download(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _story_html(page: str) -> str:
    match = re.search(r'<article[^>]*class="[^"]*reeder_chapter_article[^"]*"[^>]*>(.*?)</article>', page, re.S | re.I)
    if not match:
        raise ValueError("MirNovel story container was not found")
    article = re.sub(r'<(?:div|p)[^>]*class="[^"]*(?:disclaimer|navigation|advert)[^"]*"[^>]*>.*?</(?:div|p)>', "", match.group(1), flags=re.S | re.I)
    return article


def _html_to_text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(?:p|h[1-6]|div|li)>", "\n\n", fragment, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", fragment))
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    # The title and mandatory legal notice are not prose.
    paragraphs = [
        p for p in paragraphs
        if not (
            p.lstrip("\ufeff\u200b").startswith("Том ")
            or p.startswith("Может содержать информацию")
            or p.startswith("В тексте произведения такая информация")
            or (p.startswith("(") and ("обновлено" in p.lower() or "ред." in p.lower()))
        )
    ]
    return clean_story_text("\n\n".join(paragraphs))


def _next_url(page: str, current_url: str) -> str:
    links = re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I)
    for href, label in links:
        if "следующая" in re.sub(r"<[^>]+>", "", label).lower():
            return urljoin(current_url, unescape(href))
    # On this public reader the visible Next button calls JavaScript with the
    # next numeric chapter id instead of exposing an <a> link.
    match = re.search(r"/chapter-(\d+)(?:[/?#]|$)", current_url)
    if match:
        return current_url.replace(match.group(1), str(int(match.group(1)) + 1), 1)
    raise ValueError("MirNovel next-chapter link was not found")


def download_chapters(start_url: str, output: Path, count: int, delay_seconds: float, refresh: bool = False) -> int:
    if count < 1:
        raise ValueError("count must be at least 1")
    if delay_seconds < 2:
        raise ValueError("delay must be at least 2 seconds")
    output.mkdir(parents=True, exist_ok=True)
    url = start_url
    for index in range(1, count + 1):
        target = output / f"ch_{index:04d}_ru_fan_75.txt"
        if refresh or not target.exists():
            page = _download(url)
            text = _html_to_text(_story_html(page))
            if len(text) < 500:
                raise ValueError(f"Chapter {index} is unexpectedly short; stopped without continuing")
            target.write_text(text + "\n", encoding="utf-8")
        else:
            page = _download(url)  # Needed only to discover the next visible chapter link.
        if index < count:
            url = _next_url(page, url)
            time.sleep(delay_seconds)
    return count
