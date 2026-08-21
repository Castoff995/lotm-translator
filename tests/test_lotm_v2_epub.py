from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path

from src.lotm_v2 import NORMALIZED_SCHEMA_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION
from src.lotm_v2.domain import (
    Language, ParagraphizationMode, SourceDescriptor, SourceFormat, SourceId,
    SourceManifest, SourceRole,
)
from src.lotm_v2.infrastructure.corpus_io import chapter_from_dict, chapter_to_dict, load_manifest, save_manifest
from src.lotm_v2.infrastructure.paths import PathPolicy
from src.lotm_v2.ingest.epub import ingest_epub_package, inspect_epub, load_epub_package
from src.lotm_v2.normalize.epub_artifact import (
    artifact_from_dict, artifact_to_dict, canonical_fragment_sha256, dom_path,
    generate_artifact, load_artifact, readable_text, register_artifact,
    resolve_dom_path, save_artifact, validate_artifact,
)
from src.lotm_v2.normalize.service import normalize_manifest_chapter
from src.lotm_v2.normalize.service import normalize_text_chapter


CONTAINER = """<?xml version='1.0'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
 <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>"""


def _xhtml(body: str) -> str:
    return f"<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'><head><title>T</title></head><body>{body}</body></html>"


def make_epub(path: Path, *, epub2: bool = False, shared: bool = False, multi: bool = False, toc_only_heading: bool = False) -> Path:
    if shared:
        docs = {"text/book.xhtml": _xhtml(
            "<section id='c1'><h2>Chapter 1</h2><p>One <em>inline</em> line<br/>continues.</p>"
            "<p><ruby>漢<rt>kan</rt></ruby> base.</p><aside epub:type='footnote'><p>Note one.</p></aside>"
            "<div>Leaf block.</div><hr/></section><section id='c2'><h2>Chapter 2</h2><p>Two.</p></section>"
        )}
        nav_links = [("Chapter 1", "text/book.xhtml#c1"), ("Chapter 2", "text/book.xhtml#c2")]
    elif multi:
        docs = {
            "text/a.xhtml": _xhtml("<h2 id='c1'>Chapter 1</h2><p>Part A.</p>"),
            "text/b.xhtml": _xhtml("<p>Part B.</p>"),
            "text/c.xhtml": _xhtml("<h2 id='c2'>Chapter 2</h2><p>Next.</p>"),
        }
        nav_links = [("Chapter 1", "text/a.xhtml#c1"), ("Chapter 2", "text/c.xhtml#c2")]
    else:
        body_heading = "" if toc_only_heading else "<h2>Chapter 1</h2>"
        docs = {
            "text/c1.xhtml": _xhtml(body_heading + "<p>Alpha <strong>beta</strong>.</p><div><p>Child only.</p></div>"),
            "text/c2.xhtml": _xhtml("<h2>Chapter 2</h2><p>Gamma.</p>"),
        }
        nav_links = [("Chapter 1", "text/c1.xhtml"), ("Chapter 2", "text/c2.xhtml")]
    manifest = []
    spine = []
    for index, href in enumerate(reversed(list(docs)), start=1):  # manifest order deliberately opposes spine
        manifest.append(f"<item id='d{index}' href='{href}' media-type='application/xhtml+xml'/>")
    id_by_href = {href: f"d{len(docs)-index}" for index, href in enumerate(docs)}
    if epub2:
        manifest.append("<item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/>")
    else:
        manifest.append("<item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>")
        spine.append("<itemref idref='nav' linear='no'/>")
    for href in docs:
        spine.append(f"<itemref idref='{id_by_href[href]}'/>")
    opf = f"""<?xml version='1.0' encoding='utf-8'?>
    <package xmlns='http://www.idpf.org/2007/opf' unique-identifier='id' version='{'2.0' if epub2 else '3.0'}'>
      <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>synthetic</dc:identifier><dc:title>Synthetic</dc:title><dc:language>en</dc:language></metadata>
      <manifest>{''.join(manifest)}</manifest><spine{' toc="ncx"' if epub2 else ''}>{''.join(spine)}</spine>
    </package>"""
    nav = "<nav epub:type='toc'><ol>" + "".join(f"<li><a href='{href}'>{label}</a></li>" for label, href in nav_links) + "</ol></nav>"
    ncx = "<?xml version='1.0'?><ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'><head/><docTitle><text>Synthetic</text></docTitle><navMap>" + "".join(
        f"<navPoint id='n{i}' playOrder='{i}'><navLabel><text>{label}</text></navLabel><content src='{href}'/></navPoint>" for i, (label, href) in enumerate(nav_links, 1)
    ) + "</navMap></ncx>"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("EPUB/content.opf", opf)
        for href, content in docs.items():
            archive.writestr(f"EPUB/{href}", content)
        archive.writestr("EPUB/toc.ncx" if epub2 else "EPUB/nav.xhtml", ncx if epub2 else _xhtml(nav))
    return path


def manifest() -> SourceManifest:
    return SourceManifest(SOURCE_MANIFEST_SCHEMA_VERSION, SourceDescriptor(
        SourceId("en"), "lotm", Language.EN, SourceRole.OFFICIAL, "synthetic",
        SourceFormat.EPUB, NORMALIZED_SCHEMA_VERSION, ParagraphizationMode.EPUB_STRUCTURE,
    ))


@contextmanager
def raises(error_type, match: str | None = None):
    try:
        yield
    except error_type as error:
        if match is not None and not re.search(match, str(error)):
            raise AssertionError(f"{error!r} does not match {match!r}") from error
    else:
        raise AssertionError(f"Expected {error_type.__name__}")


def case_epub3_nav_spine_and_inspection(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    package = load_epub_package(book)
    assert [item.href for item in package.spine if item.linear] == ["EPUB/text/c1.xhtml", "EPUB/text/c2.xhtml"]
    assert [entry.label for entry in package.navigation] == ["Chapter 1", "Chapter 2"]
    assert len(package.package_sha256) == 64
    report = inspect_epub(book)
    assert report["package"]["has_navigation_document"] is True
    assert report["spine"][0]["linear"] is False


def case_package_sha256_matches_independent_hashlib(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    expected = hashlib.sha256(book.read_bytes()).hexdigest()
    package = load_epub_package(book)
    assert package.package_sha256 == expected
    paths = PathPolicy(tmp_path / "corpus")
    manifest_path = paths.manifest(SourceId("en"))
    save_manifest(manifest_path, manifest())
    ingested, raw_path = ingest_epub_package(book, manifest(), manifest_path, 1, paths)
    assert ingested.chapter(1).sha256 == expected
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == expected


def case_epub2_ncx_discovery(tmp_path: Path):
    package = load_epub_package(make_epub(tmp_path / "book.epub", epub2=True))
    assert package.version == "2.0"
    assert package.navigation[0].label == "Chapter 1"


def case_dom_structure_inline_br_ruby_footnote_leaf_and_hr(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub", shared=True)
    artifact = generate_artifact(book, manifest(), 1)
    assert len(artifact.segments) == 1
    assert artifact.segments[0].start_dom_path and artifact.segments[0].end_dom_path
    assert [group[0].structural_type for group in artifact.paragraphs].count("heading") == 1
    assert [group[0].structural_type for group in artifact.paragraphs].count("footnote") == 1
    assert any(group[0].has_ruby for group in artifact.paragraphs)
    assert len(artifact.structural_events) == 1
    package = load_epub_package(book)
    validate_artifact(artifact, package, manifest(), 1)
    ruby_ref = next(group[0] for group in artifact.paragraphs if group[0].has_ruby)
    from src.lotm_v2.normalize.epub_artifact import _parse_document
    from src.lotm_v2.ingest.epub import document_bytes
    root = _parse_document(document_bytes(package, ruby_ref.document_href), ruby_ref.document_href)
    assert readable_text(resolve_dom_path(root, ruby_ref.dom_path)) == "漢 base."
    assert "\n" in readable_text(next(e for e in root.iter() if getattr(e, "tag", "").endswith("p")))


def case_container_does_not_duplicate_child(tmp_path: Path):
    artifact = generate_artifact(make_epub(tmp_path / "book.epub"), manifest(), 1)
    assert [group[0].element_tag for group in artifact.paragraphs] == ["h2", "p", "p"]


def case_toc_only_heading_is_not_fabricated(tmp_path: Path):
    artifact = generate_artifact(make_epub(tmp_path / "book.epub", toc_only_heading=True), manifest(), 1)
    assert [group[0].element_tag for group in artifact.paragraphs] == ["p", "p"]
    assert all(group[0].structural_type != "heading" for group in artifact.paragraphs)


def case_chapter_can_span_multiple_documents(tmp_path: Path):
    artifact = generate_artifact(make_epub(tmp_path / "book.epub", multi=True), manifest(), 1)
    assert [segment.document_href for segment in artifact.segments] == ["EPUB/text/a.xhtml", "EPUB/text/b.xhtml"]


def case_dom_path_and_fragment_hash_round_trip(tmp_path: Path):
    package = load_epub_package(make_epub(tmp_path / "book.epub"))
    from src.lotm_v2.normalize.epub_artifact import _parse_document
    from src.lotm_v2.ingest.epub import document_bytes
    root = _parse_document(document_bytes(package, "EPUB/text/c1.xhtml"), "x")
    paragraph = next(item for item in root.iter() if getattr(item, "tag", "").endswith("p"))
    assert resolve_dom_path(root, dom_path(paragraph)) is paragraph
    assert canonical_fragment_sha256(paragraph) == canonical_fragment_sha256(resolve_dom_path(root, dom_path(paragraph)))


def case_immutable_epub_rejects_replacement(tmp_path: Path):
    paths = PathPolicy(tmp_path)
    first = make_epub(tmp_path / "a.epub")
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest_path, manifest())
    updated, _ = ingest_epub_package(first, manifest(), manifest_path, 1, paths)
    second = make_epub(tmp_path / "b.epub", shared=True)
    with raises(ValueError, match="different content"):
        ingest_epub_package(second, updated, manifest_path, 1, paths)


def case_artifact_hash_path_order_and_coverage_fail_closed(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    artifact = generate_artifact(book, manifest(), 1)
    package = load_epub_package(book)
    payload = artifact_to_dict(artifact)
    payload["raw_epub_sha256"] = "0" * 64
    with raises(ValueError, match="package checksum"):
        validate_artifact(artifact_from_dict(payload), package, manifest(), 1)
    payload = artifact_to_dict(artifact)
    payload["paragraphs"][0]["fragments"][0]["fragment_sha256"] = "0" * 64
    with raises(ValueError, match="provenance/hash"):
        validate_artifact(artifact_from_dict(payload), package, manifest(), 1)
    payload = artifact_to_dict(artifact)
    payload["paragraphs"][0]["fragments"][0]["dom_path"] = "/html[1]/body[1]/p[999]"
    with raises(ValueError):
        validate_artifact(artifact_from_dict(payload), package, manifest(), 1)
    payload = artifact_to_dict(artifact)
    payload["paragraphs"] = list(reversed(payload["paragraphs"]))
    with raises(ValueError, match="non-monotonic"):
        validate_artifact(artifact_from_dict(payload), package, manifest(), 1)
    payload = artifact_to_dict(artifact)
    payload["paragraphs"] = payload["paragraphs"][:-1]
    with raises(ValueError, match="cover every"):
        validate_artifact(artifact_from_dict(payload), package, manifest(), 1)


def case_document_sha256_mismatch_fails_closed(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    artifact = generate_artifact(book, manifest(), 1)
    payload = artifact_to_dict(artifact)
    payload["paragraphs"][0]["fragments"][0]["document_sha256"] = "0" * 64
    with raises(ValueError, match="provenance/hash mismatch"):
        validate_artifact(artifact_from_dict(payload), load_epub_package(book), manifest(), 1)


def case_duplicate_epub_fragment_ref_fails_closed(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    artifact = generate_artifact(book, manifest(), 1)
    payload = artifact_to_dict(artifact)
    duplicate = json.loads(json.dumps(payload["paragraphs"][0]))
    payload["paragraphs"].append(duplicate)
    with raises(ValueError, match="revisit|duplicate|coverage"):
        validate_artifact(artifact_from_dict(payload), load_epub_package(book), manifest(), 1)


def case_registered_artifact_normalizes_with_dom_provenance(tmp_path: Path):
    paths = PathPolicy(tmp_path)
    manifest_path = paths.manifest(SourceId("en"))
    save_manifest(manifest_path, manifest())
    book = make_epub(tmp_path / "input.epub", shared=True)
    ingested, raw = ingest_epub_package(book, manifest(), manifest_path, 1, paths)
    artifact = generate_artifact(raw, ingested, 1)
    artifact_path = paths.epub_artifact(SourceId("en"), 1)
    save_artifact(artifact_path, artifact)
    frozen = register_artifact(ingested, manifest_path, artifact_path, 1, paths)
    assert load_artifact(artifact_path).status == "confirmed"
    chapter = normalize_manifest_chapter(frozen, manifest_path, 1, paths)
    assert chapter.schema_version == NORMALIZED_SCHEMA_VERSION
    assert chapter.paragraphs[0].paragraph_type.value == "heading"
    assert chapter.paragraphs[-2].paragraph_type.value == "footnote"
    assert chapter.paragraphs[1].raw_text.count("\n") == 1
    assert chapter.paragraphs[2].raw_text == "漢 base."
    assert chapter.paragraphs[0].provenance.location_kind.value == "epub_dom"
    assert chapter_from_dict(chapter_to_dict(chapter)) == chapter
    # Registered artifact bytes cannot drift silently.
    artifact_path.write_text(artifact_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with raises(ValueError, match="artifact changed"):
        normalize_manifest_chapter(frozen, manifest_path, 1, paths)


def case_artifact_contains_no_full_paragraph_text(tmp_path: Path):
    artifact = generate_artifact(make_epub(tmp_path / "book.epub"), manifest(), 1)
    serialized = json.dumps(artifact_to_dict(artifact))
    assert "Alpha" not in serialized and "Child only" not in serialized


def case_malformed_xhtml_fails_strictly(tmp_path: Path):
    book = make_epub(tmp_path / "book.epub")
    with zipfile.ZipFile(book) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["EPUB/text/c1.xhtml"] = b"<html><body><p>broken</body></html>"
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(broken, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    with raises(ValueError, match="Malformed XHTML"):
        generate_artifact(broken, manifest(), 1)


def case_schema_files_parse_and_optional_dependency_message(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    for name in ("source-manifest.schema.json", "normalized-chapter.schema.json", "epub-paragraphization-artifact.schema.json"):
        assert isinstance(json.loads((root / "schemas" / "v2" / name).read_text(encoding="utf-8")), dict)
    from src.lotm_v2.ingest.epub import EPUB_UNAVAILABLE, require_epub_dependencies
    real_import = __import__
    def blocked(name, *args, **kwargs):
        if name == "ebooklib" or name.startswith("ebooklib."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=blocked):
        with raises(ValueError, match=re.escape(EPUB_UNAVAILABLE)):
            require_epub_dependencies()


def case_legacy_line_span_without_location_kind_is_backward_readable(tmp_path: Path):
    legacy_manifest = SourceManifest(SOURCE_MANIFEST_SCHEMA_VERSION, SourceDescriptor(
        SourceId("en"), "lotm", Language.EN, SourceRole.OFFICIAL, "legacy text",
        SourceFormat.TEXT, NORMALIZED_SCHEMA_VERSION, ParagraphizationMode.BLANK_LINES,
    ))
    raw = "first physical line\ncontinuation"
    chapter = normalize_text_chapter(
        raw, legacy_manifest, Path("manifest.json"), 1, "raw/ch_0001.txt",
        hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    payload = chapter_to_dict(chapter)
    provenance = payload["paragraphs"][0]["provenance"]
    del provenance["location_kind"]
    del provenance["epub_fragments"]
    loaded = chapter_from_dict(payload)
    assert loaded == chapter
    assert loaded.paragraphs[0].provenance.location_kind.value == "line_span"
    assert (loaded.paragraphs[0].provenance.start_line, loaded.paragraphs[0].provenance.end_line) == (1, 2)


def case_epub_normalization_has_no_alignment_hints_or_gold_dependency(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    production_modules = (
        root / "src" / "lotm_v2" / "ingest" / "epub.py",
        root / "src" / "lotm_v2" / "normalize" / "epub.py",
        root / "src" / "lotm_v2" / "normalize" / "epub_artifact.py",
    )
    forbidden = {"alignment", "hints", "gold"}
    violations: list[str] = []
    for path in production_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for module_name in imported:
                components = {part.lower() for part in module_name.split(".")}
                if components & forbidden:
                    violations.append(f"{path.name}:{node.lineno}:{module_name}")
    assert violations == []


class StructuredEpubTests(unittest.TestCase):
    def run_case(self, function) -> None:
        with tempfile.TemporaryDirectory() as directory:
            function(Path(directory))

    def test_epub3_nav_spine_and_inspection(self): self.run_case(case_epub3_nav_spine_and_inspection)
    def test_package_sha256_matches_independent_hashlib(self): self.run_case(case_package_sha256_matches_independent_hashlib)
    def test_epub2_ncx_discovery(self): self.run_case(case_epub2_ncx_discovery)
    def test_dom_structure_inline_br_ruby_footnote_leaf_and_hr(self): self.run_case(case_dom_structure_inline_br_ruby_footnote_leaf_and_hr)
    def test_container_does_not_duplicate_child(self): self.run_case(case_container_does_not_duplicate_child)
    def test_toc_only_heading_is_not_fabricated(self): self.run_case(case_toc_only_heading_is_not_fabricated)
    def test_chapter_can_span_multiple_documents(self): self.run_case(case_chapter_can_span_multiple_documents)
    def test_dom_path_and_fragment_hash_round_trip(self): self.run_case(case_dom_path_and_fragment_hash_round_trip)
    def test_immutable_epub_rejects_replacement(self): self.run_case(case_immutable_epub_rejects_replacement)
    def test_artifact_hash_path_order_and_coverage_fail_closed(self): self.run_case(case_artifact_hash_path_order_and_coverage_fail_closed)
    def test_document_sha256_mismatch_fails_closed(self): self.run_case(case_document_sha256_mismatch_fails_closed)
    def test_duplicate_epub_fragment_ref_fails_closed(self): self.run_case(case_duplicate_epub_fragment_ref_fails_closed)
    def test_registered_artifact_normalizes_with_dom_provenance(self): self.run_case(case_registered_artifact_normalizes_with_dom_provenance)
    def test_artifact_contains_no_full_paragraph_text(self): self.run_case(case_artifact_contains_no_full_paragraph_text)
    def test_malformed_xhtml_fails_strictly(self): self.run_case(case_malformed_xhtml_fails_strictly)
    def test_schema_files_parse_and_optional_dependency_message(self): self.run_case(case_schema_files_parse_and_optional_dependency_message)
    def test_legacy_line_span_without_location_kind_is_backward_readable(self): self.run_case(case_legacy_line_span_without_location_kind_is_backward_readable)
    def test_epub_normalization_has_no_alignment_hints_or_gold_dependency(self): self.run_case(case_epub_normalization_has_no_alignment_hints_or_gold_dependency)
