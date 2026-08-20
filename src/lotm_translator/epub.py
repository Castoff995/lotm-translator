from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class Chapter:
    order: int
    title: str
    href: str


class _TextExtractor(HTMLParser):
    _block_tags = {"article", "br", "div", "h1", "h2", "h3", "li", "p", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag.lower() in self._block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag.lower() in self._block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\xa0", " ")
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]*\n+", "\n\n", raw)
        return raw.strip()


def _normalized_href(base: str, href: str) -> str:
    path = href.split("#", maxsplit=1)[0]
    return str((PurePosixPath(base).parent / path).as_posix())


def chapters(epub_path: Path) -> list[Chapter]:
    """Read the EPUB NCX navigation map in reading order."""
    with zipfile.ZipFile(epub_path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(element.attrib["full-path"] for element in container.iter() if element.tag.endswith("rootfile"))
        package = ET.fromstring(archive.read(rootfile))
        ncx_id = next(
            item.attrib["id"]
            for item in package.iter()
            if item.tag.endswith("item") and item.attrib.get("media-type") == "application/x-dtbncx+xml"
        )
        ncx_href = next(item.attrib["href"] for item in package.iter() if item.tag.endswith("item") and item.attrib.get("id") == ncx_id)
        ncx_path = _normalized_href(rootfile, ncx_href)
        navigation = ET.fromstring(archive.read(ncx_path))

    result: list[Chapter] = []
    for node in navigation.iter():
        if not node.tag.endswith("navPoint"):
            continue
        label = next((text.text or "").strip() for text in node.iter() if text.tag.endswith("text"))
        content = next((item.attrib.get("src", "") for item in node.iter() if item.tag.endswith("content")), "")
        if label and content:
            result.append(Chapter(len(result) + 1, label, _normalized_href(ncx_path, content)))
    return result


def chapter_text(epub_path: Path, chapter: Chapter) -> str:
    with zipfile.ZipFile(epub_path) as archive:
        document = archive.read(chapter.href).decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(document)
    return parser.text()


def safe_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    return cleaned[:100] or "untitled"


def extract(epub_path: Path, output_dir: Path, language: str, count: int, start: int = 1) -> list[Chapter]:
    selected = chapters(epub_path)[start - 1 : start - 1 + count]
    output_dir.mkdir(parents=True, exist_ok=True)
    for chapter in selected:
        filename = f"{chapter.order:04d}_{safe_filename(chapter.title)}.txt"
        lines = chapter_text(epub_path, chapter).splitlines()
        resource_name = PurePosixPath(chapter.href).name
        while lines and (not lines[0].strip() or lines[0].strip() in {chapter.title, resource_name}):
            lines.pop(0)
        body = "\n".join(lines).strip()
        content = f"# {chapter.title}\n\n{body}\n"
        (output_dir / filename).write_text(content, encoding="utf-8")
    manifest = {
        "source_epub": epub_path.name,
        "language": language,
        "toc_start": start,
        "chapter_count": len(selected),
        "chapters": [asdict(chapter) for chapter in selected],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return selected
