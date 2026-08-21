"""Optional immutable EPUB package ingestion and container inspection."""
from __future__ import annotations

import hashlib
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote

from ..domain import SourceChapter, SourceFormat, SourceManifest
from ..infrastructure.corpus_io import save_manifest
from ..infrastructure.paths import PathPolicy


EPUB_UNAVAILABLE = "Structured EPUB support is unavailable; install requirements-epub.txt"


def require_epub_dependencies():
    try:
        from ebooklib import epub
        from lxml import etree
    except ImportError as error:  # pragma: no cover - exercised by isolated import test
        raise ValueError(EPUB_UNAVAILABLE) from error
    return epub, etree


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SpineDocument:
    index: int
    item_id: str
    href: str
    linear: bool
    media_type: str
    properties: tuple[str, ...] = ()

    @property
    def is_navigation(self) -> bool:
        return "nav" in self.properties


@dataclass(frozen=True)
class NavigationEntry:
    label: str
    href: str
    fragment: str | None
    depth: int


@dataclass(frozen=True)
class EpubPackage:
    path: Path
    package_sha256: str
    version: str
    package_document: str
    title: str | None
    spine: tuple[SpineDocument, ...]
    navigation: tuple[NavigationEntry, ...]
    has_navigation_document: bool


def _archive_href(package_document: str, href: str) -> str:
    clean = unquote(href.split("#", 1)[0]).replace("\\", "/")
    base = str(PurePosixPath(package_document).parent)
    if base == ".":
        base = ""
    candidate = posixpath.normpath(posixpath.join(base, clean))
    return candidate.lstrip("/")


def _container_package_path(archive: zipfile.ZipFile, etree: Any) -> str:
    try:
        root = etree.fromstring(
            archive.read("META-INF/container.xml"),
            parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False),
        )
        values = root.xpath("//*[local-name()='rootfile']/@full-path")
    except Exception as error:
        raise ValueError(f"Invalid EPUB container.xml: {error}") from error
    if len(values) != 1:
        raise ValueError("EPUB must declare exactly one package rootfile")
    return str(values[0])


def _flatten_toc(nodes: Iterable[Any], package_document: str, depth: int = 0) -> list[NavigationEntry]:
    result: list[NavigationEntry] = []
    for node in nodes:
        if isinstance(node, tuple):
            section, children = node
            href = getattr(section, "href", "") or ""
            title = str(getattr(section, "title", "") or "").strip()
            if href and title:
                document, _, fragment = href.partition("#")
                result.append(NavigationEntry(title, _archive_href(package_document, document), fragment or None, depth))
            result.extend(_flatten_toc(children, package_document, depth + 1))
            continue
        href = str(getattr(node, "href", "") or "")
        title = str(getattr(node, "title", "") or "").strip()
        if href and title:
            document, _, fragment = href.partition("#")
            result.append(NavigationEntry(title, _archive_href(package_document, document), fragment or None, depth))
    return result


def load_epub_package(path: Path) -> EpubPackage:
    epub, etree = require_epub_dependencies()
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"EPUB package does not exist: {resolved}")
    try:
        book = epub.read_epub(str(resolved), options={"ignore_ncx": False})
    except Exception as error:
        raise ValueError(f"Cannot read EPUB package {resolved}: {error}") from error
    with zipfile.ZipFile(resolved) as archive:
        package_document = _container_package_path(archive, etree)
        package_bytes = archive.read(package_document)
    try:
        package_root = etree.fromstring(
            package_bytes,
            parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False),
        )
    except Exception as error:
        raise ValueError(f"Malformed EPUB package document {package_document}: {error}") from error
    manifest_items: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for item in package_root.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        item_id = str(item.get("id", ""))
        manifest_items[item_id] = (
            _archive_href(package_document, str(item.get("href", ""))),
            str(item.get("media-type", "")),
            tuple(filter(None, str(item.get("properties", "")).split())),
        )
    spine: list[SpineDocument] = []
    for index, itemref in enumerate(package_root.xpath("//*[local-name()='spine']/*[local-name()='itemref']")):
        item_id = str(itemref.get("idref", ""))
        if item_id not in manifest_items:
            raise ValueError(f"Spine references missing manifest item: {item_id}")
        href, media_type, properties = manifest_items[item_id]
        spine.append(SpineDocument(
            index, item_id, href, str(itemref.get("linear", "yes")).lower() != "no",
            media_type, properties,
        ))
    title_values = book.get_metadata("DC", "title")
    title = str(title_values[0][0]).strip() if title_values else None
    navigation = tuple(_flatten_toc(book.toc, package_document))
    return EpubPackage(
        resolved, sha256_file(resolved), str(getattr(book, "version", "unknown")),
        package_document, title, tuple(spine), navigation,
        any(item.is_navigation for item in spine) or any("nav" in props for _, _, props in manifest_items.values()),
    )


def document_bytes(package: EpubPackage, href: str) -> bytes:
    try:
        with zipfile.ZipFile(package.path) as archive:
            return archive.read(href)
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError(f"Missing XHTML document in EPUB: {href}") from error


def inspect_epub(path: Path) -> dict[str, Any]:
    _, etree = require_epub_dependencies()
    package = load_epub_package(path)
    documents: list[dict[str, Any]] = []
    mapped = {item.href for item in package.navigation}
    for item in package.spine:
        info: dict[str, Any] = {
            "spine_index": item.index, "item_id": item.item_id, "href": item.href,
            "linear": item.linear, "navigation_document": item.is_navigation,
            "mapped_by_navigation": item.href in mapped, "parse_error": None,
            "heading_preview": None, "block_tags": {},
        }
        if item.media_type in {"application/xhtml+xml", "text/html"}:
            try:
                root = etree.fromstring(
                    document_bytes(package, item.href),
                    parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False),
                )
                counts: dict[str, int] = {}
                for element in root.iter():
                    tag = etree.QName(element).localname.lower()
                    if tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption", "div", "aside", "hr", "br", "ruby"}:
                        counts[tag] = counts.get(tag, 0) + 1
                headings = root.xpath("//*[local-name()='h1' or local-name()='h2' or local-name()='h3']")
                if headings:
                    info["heading_preview"] = " ".join("".join(headings[0].itertext()).split())[:120]
                info["block_tags"] = counts
            except Exception as error:
                info["parse_error"] = str(error)
        documents.append(info)
    return {
        "package": {
            "path": str(package.path), "sha256": package.package_sha256,
            "epub_version": package.version, "package_document": package.package_document,
            "title": package.title, "has_navigation_document": package.has_navigation_document,
        },
        "spine": documents,
        "navigation": [entry.__dict__ for entry in package.navigation],
        "unmapped_linear_documents": [
            item.href for item in package.spine
            if item.linear and not item.is_navigation and item.href not in mapped
        ],
    }


def ingest_epub_package(
    input_path: Path, manifest: SourceManifest, manifest_path: Path,
    chapter_number: int, paths: PathPolicy,
) -> tuple[SourceManifest, Path]:
    if manifest.descriptor.source_format is not SourceFormat.EPUB:
        raise ValueError("ingest-epub requires a manifest with format=epub")
    content = input_path.read_bytes()
    # Validate the container before it becomes immutable authority.
    load_epub_package(input_path)
    digest = hashlib.sha256(content).hexdigest()
    target = paths.raw_epub(manifest.descriptor.source_id)
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError(f"Immutable raw EPUB already exists with different content: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    updated = manifest.with_chapter(SourceChapter(chapter_number, paths.relative(target), digest))
    save_manifest(manifest_path, updated)
    return updated, target
