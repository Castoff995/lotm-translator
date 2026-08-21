"""Raw source ingestion; no alignment or grouping responsibilities."""

from .text import ingest_text_chapter
from .epub import EPUB_UNAVAILABLE, ingest_epub_package, inspect_epub

__all__ = ["EPUB_UNAVAILABLE", "ingest_epub_package", "ingest_text_chapter", "inspect_epub"]
