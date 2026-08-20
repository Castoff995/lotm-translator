from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .epub import chapters, extract
from .alignment import build_chapter_alignment
from .audit import audit_aligned_fan_translation, audit_bertalign_fan_translation, audit_embedding_aligned_fan_translation, audit_fan_translation
from .mirnovel import download_chapters
from .ocr import assemble_ocr_chapter, clean_ocr_chapter_for_review, find_tesseract, ocr_images
from .ocr_review import review_ocr_candidates
from .ollama import translate_sample
from .qwen_alignment import prepare_alignment
from .sequential_alignment import align_sequential
from .embedding_alignment import align_pair, review_boundary, validate_pair
from .text import clean_story_text


def import_text_directory(source: Path, output: Path, language: str, source_label: str) -> int:
    files = sorted(source.glob("*.txt"))
    if not files:
        raise ValueError(f"No .txt files found in {source}")
    output.mkdir(parents=True, exist_ok=True)
    chapter_entries = []
    for file in files:
        cleaned = clean_story_text(file.read_text(encoding="utf-8"))
        target = output / file.name
        target.write_text(cleaned, encoding="utf-8")
        chapter_entries.append({"source_file": file.name, "corpus_file": target.name})
    (output / "manifest.json").write_text(
        json.dumps({"language": language, "source_label": source_label, "chapter_count": len(files), "chapters": chapter_entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a local EPUB translation corpus.")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_command = commands.add_parser("inspect", help="Print EPUB chapters from its table of contents.")
    inspect_command.add_argument("epub", type=Path)

    extract_command = commands.add_parser("extract", help="Extract the first EPUB chapters into UTF-8 text files.")
    extract_command.add_argument("epub", type=Path)
    extract_command.add_argument("output", type=Path)
    extract_command.add_argument("--language", required=True, choices=("zh", "en", "ru"))
    extract_command.add_argument("--start", type=int, default=1, help="1-based position in the EPUB table of contents")
    extract_command.add_argument("--count", type=int, default=5)

    import_command = commands.add_parser("import-text", help="Import UTF-8 chapter files and remove footnote markers.")
    import_command.add_argument("source", type=Path)
    import_command.add_argument("output", type=Path)
    import_command.add_argument("--language", required=True, choices=("zh", "en", "ru"))
    import_command.add_argument("--source-label", required=True)

    align_command = commands.add_parser("align-chapters", help="Create a reviewed chapter-level multilingual alignment manifest.")
    align_command.add_argument("--zh", type=Path, required=True)
    align_command.add_argument("--en", type=Path, required=True)
    align_command.add_argument("--ru-fan", type=Path, required=True)
    align_command.add_argument("--output", type=Path, required=True)

    translate_command = commands.add_parser("translate-sample", help="Translate a short local sample through Ollama.")
    translate_command.add_argument("--zh", type=Path, required=True)
    translate_command.add_argument("--en", type=Path, required=True)
    translate_command.add_argument("--glossary", type=Path, default=Path("config/glossary.json"))
    translate_command.add_argument("--output", type=Path, required=True)
    translate_command.add_argument("--model", default="qwen3:14b")
    translate_command.add_argument("--paragraphs", type=int, default=5)

    ocr_command = commands.add_parser("ocr-russian", help="Run local Russian OCR on scanned page images.")
    ocr_command.add_argument("input", type=Path, help="Directory containing JPG, PNG, TIFF, or WEBP page scans")
    ocr_command.add_argument("output", type=Path)
    ocr_command.add_argument("--tesseract", type=Path, help="Optional explicit tesseract.exe path")
    ocr_command.add_argument("--psm", type=int, default=6, help="Tesseract page segmentation mode (default: 6)")
    ocr_command.add_argument("--no-preprocess", action="store_true", help="Use the original images without local crop/contrast preparation.")

    assemble_ocr_command = commands.add_parser("assemble-ocr-chapter", help="Join consecutive OCR page files into a reviewable chapter draft.")
    assemble_ocr_command.add_argument("pages", type=Path, help="Directory containing per-page OCR .txt files")
    assemble_ocr_command.add_argument("output", type=Path)
    assemble_ocr_command.add_argument("--from", dest="first_stem", required=True, help="First page filename without .txt, e.g. IMG_8486")
    assemble_ocr_command.add_argument("--to", dest="last_stem", required=True, help="Last page filename without .txt, e.g. IMG_8491")
    assemble_ocr_command.add_argument("--chapter", type=int, required=True)
    assemble_ocr_command.add_argument("--title", required=True)
    assemble_ocr_command.add_argument("--printed-pages", required=True, help="Physical page range, e.g. 5-10")

    clean_ocr_command = commands.add_parser("clean-ocr-chapter", help="Mechanically clean an OCR chapter and list remaining doubtful lines.")
    clean_ocr_command.add_argument("input", type=Path)
    clean_ocr_command.add_argument("output", type=Path)
    clean_ocr_command.add_argument("report", type=Path)

    review_ocr_command = commands.add_parser("review-ocr-candidates", help="Ask local Qwen to classify OCR candidates; does not edit OCR text.")
    review_ocr_command.add_argument("review", type=Path)
    review_ocr_command.add_argument("english", type=Path)
    review_ocr_command.add_argument("output", type=Path)
    review_ocr_command.add_argument("--model", default="qwen3:14b")

    ocr_ui_command = commands.add_parser("review-ocr-ui", help="Open the local OCR candidate review window.")
    ocr_ui_command.add_argument("qwen_review", type=Path)
    ocr_ui_command.add_argument("candidates", type=Path)
    ocr_ui_command.add_argument("--images", type=Path, default=Path("lotm/ocr_input"))
    mirnovel_command = commands.add_parser("download-mirnovel", help="Download public MirNovel chapters slowly into local ignored data.")
    mirnovel_command.add_argument("start_url")
    mirnovel_command.add_argument("output", type=Path)
    mirnovel_command.add_argument("--count", type=int, default=10)
    mirnovel_command.add_argument("--delay", type=float, default=3.0)
    mirnovel_command.add_argument("--refresh", action="store_true", help="Re-download chapters and replace local cached copies.")

    qwen_align_command = commands.add_parser("qwen-align", help="Ask local Qwen to align paragraph windows for matching chapters.")
    qwen_align_command.add_argument("--zh", type=Path, required=True)
    qwen_align_command.add_argument("--en", type=Path, required=True)
    qwen_align_command.add_argument("--ru", type=Path, required=True)
    qwen_align_command.add_argument("--output", type=Path, required=True)
    qwen_align_command.add_argument("--model", default="qwen3:14b")
    qwen_align_command.add_argument("--start-chapter", type=int, default=1)
    qwen_align_command.add_argument("--end-chapter", type=int, help="Last chapter number to process (inclusive).")

    audit_command = commands.add_parser("audit-fan", help="Audit fan translation additions, omissions, reordering, and meaning shifts through local Qwen.")
    audit_command.add_argument("--zh", type=Path, required=True)
    audit_command.add_argument("--en", type=Path, required=True)
    audit_command.add_argument("--ru", type=Path, required=True)
    audit_command.add_argument("--output", type=Path, required=True)
    audit_command.add_argument("--model", default="qwen3:14b")

    aligned_audit_command = commands.add_parser("audit-fan-aligned", help="Audit fan translation using existing paragraph alignment.")
    aligned_audit_command.add_argument("--zh", type=Path, required=True)
    aligned_audit_command.add_argument("--en", type=Path, required=True)
    aligned_audit_command.add_argument("--ru", type=Path, required=True)
    aligned_audit_command.add_argument("--alignment-first5", type=Path, required=True)
    aligned_audit_command.add_argument("--alignment-rest", type=Path, required=True)
    aligned_audit_command.add_argument("--output", type=Path, required=True)
    aligned_audit_command.add_argument("--model", default="qwen3:14b")
    aligned_audit_command.add_argument("--count", type=int, default=3, help="Number of initial chapters to audit (default: 3).")

    embedding_audit_command = commands.add_parser("audit-fan-bge", help="Audit fan translation using BGE-M3 pair alignments joined through English.")
    embedding_audit_command.add_argument("--zh", type=Path, required=True)
    embedding_audit_command.add_argument("--en", type=Path, required=True)
    embedding_audit_command.add_argument("--ru", type=Path, required=True)
    embedding_audit_command.add_argument("--zh-en-alignments", type=Path, required=True)
    embedding_audit_command.add_argument("--en-ru-alignments", type=Path, required=True)
    embedding_audit_command.add_argument("--output", type=Path, required=True)
    embedding_audit_command.add_argument("--model", default="qwen3:14b")
    embedding_audit_command.add_argument("--count", type=int, default=3)

    bertalign_audit_command = commands.add_parser("audit-fan-bertalign", help="Audit fan translation using fixed Bertalign groups joined through English.")
    bertalign_audit_command.add_argument("--zh", type=Path, required=True)
    bertalign_audit_command.add_argument("--en", type=Path, required=True)
    bertalign_audit_command.add_argument("--ru", type=Path, required=True)
    bertalign_audit_command.add_argument("--zh-en-alignments", type=Path, required=True)
    bertalign_audit_command.add_argument("--en-ru-alignments", type=Path, required=True)
    bertalign_audit_command.add_argument("--output", type=Path, required=True)
    bertalign_audit_command.add_argument("--model", default="qwen3:14b")
    bertalign_audit_command.add_argument("--count", type=int, default=3)

    sequential_command = commands.add_parser("qwen-align-sequential", help="Create coverage-validated many-to-many paragraph alignment through local Qwen.")
    sequential_command.add_argument("--zh", type=Path, required=True)
    sequential_command.add_argument("--en", type=Path, required=True)
    sequential_command.add_argument("--ru", type=Path, required=True)
    sequential_command.add_argument("--output", type=Path, required=True)
    sequential_command.add_argument("--model", default="qwen3:14b")
    sequential_command.add_argument("--count", type=int, default=3)

    embedding_command = commands.add_parser("embed-align-pair", help="Align two chapter files locally with BGE-M3 embeddings and dynamic programming.")
    embedding_command.add_argument("left", type=Path)
    embedding_command.add_argument("right", type=Path)
    embedding_command.add_argument("output", type=Path)
    embedding_command.add_argument("--model-path", type=Path, default=Path("models/embeddings/bge-m3"))
    embedding_command.add_argument("--device", choices=("cpu", "dml"), default="dml", help="dml is the default and uses the AMD GPU; cpu is the fallback")

    bertalign_command = commands.add_parser("bertalign-pair", help="Align a pair of locally cleaned chapter files with Bertalign + LaBSE.")
    bertalign_command.add_argument("left", type=Path)
    bertalign_command.add_argument("right", type=Path)
    bertalign_command.add_argument("output", type=Path)
    bertalign_command.add_argument("--max-align", type=int, default=5)

    validate_embedding_command = commands.add_parser("validate-bge-pair", help="Stop at the first weak BGE alignment group and save its review context.")
    validate_embedding_command.add_argument("alignment", type=Path)
    validate_embedding_command.add_argument("left", type=Path)
    validate_embedding_command.add_argument("right", type=Path)
    validate_embedding_command.add_argument("output", type=Path)
    validate_embedding_command.add_argument("--minimum-similarity", type=float, default=0.65)

    boundary_command = commands.add_parser("review-bge-boundary", help="Ask local Qwen to review the first weak BGE boundary; does not alter alignments.")
    boundary_command.add_argument("validation", type=Path)
    boundary_command.add_argument("output", type=Path)
    boundary_command.add_argument("--model", default="qwen3:14b")

    monitor_command = commands.add_parser("monitor", help="Open the local LOTM process and progress monitor (Windows).")

    review_command = commands.add_parser("review-audit", help="Open the local approval window for Qwen audit findings.")
    review_command.add_argument("--audit-dir", type=Path, default=Path("data/processed/fan_75_audit_bertalign_first3"))

    args = parser.parse_args()
    if args.command == "inspect":
        for chapter in chapters(args.epub):
            print(f"{chapter.order:04d}\t{chapter.title}\t{chapter.href}")
    elif args.command == "extract":
        if args.count < 1 or args.start < 1:
            parser.error("--start and --count must be at least 1")
        selected = extract(args.epub, args.output, args.language, args.count, args.start)
        print(f"Extracted {len(selected)} chapters to {args.output}")
    elif args.command == "import-text":
        try:
            count = import_text_directory(args.source, args.output, args.language, args.source_label)
        except ValueError as error:
            parser.error(str(error))
        print(f"Imported {count} chapters to {args.output}")
    elif args.command == "align-chapters":
        records = build_chapter_alignment(args.zh, args.en, args.ru_fan, args.output)
        print(f"Matched {len(records)} chapters in {args.output}")
    elif args.command == "translate-sample":
        if args.paragraphs < 1:
            parser.error("--paragraphs must be at least 1")
        translate_sample(args.zh, args.en, args.glossary, args.output, args.model, args.paragraphs)
        print(f"Translation written to {args.output}")
    elif args.command == "download-mirnovel":
        try:
            count = download_chapters(args.start_url, args.output, args.count, args.delay, args.refresh)
        except ValueError as error:
            parser.error(str(error))
        print(f"Downloaded {count} MirNovel chapters to {args.output}")
    elif args.command == "qwen-align":
        try:
            count = prepare_alignment(args.zh, args.en, args.ru, args.output, args.model, start_chapter=args.start_chapter, end_chapter=args.end_chapter)
        except ValueError as error:
            parser.error(str(error))
        print(f"Qwen prepared {count} chapter alignment files in {args.output}")
    elif args.command == "audit-fan":
        try:
            count = audit_fan_translation(args.zh, args.en, args.ru, args.output, args.model)
        except ValueError as error:
            parser.error(str(error))
        print(f"Qwen audited {count} chapters in {args.output}")
    elif args.command == "audit-fan-aligned":
        try:
            count = audit_aligned_fan_translation(args.zh, args.en, args.ru, args.alignment_first5, args.alignment_rest, args.output, args.model, args.count)
        except ValueError as error:
            parser.error(str(error))
        print(f"Qwen audited {count} aligned chapters in {args.output}")
    elif args.command == "audit-fan-bge":
        try:
            count = audit_embedding_aligned_fan_translation(args.zh, args.en, args.ru, args.zh_en_alignments, args.en_ru_alignments, args.output, args.model, args.count)
        except (ValueError, RuntimeError) as error:
            parser.error(str(error))
        print(f"Qwen audited {count} BGE-aligned chapters in {args.output}")
    elif args.command == "audit-fan-bertalign":
        try:
            count = audit_bertalign_fan_translation(args.zh, args.en, args.ru, args.zh_en_alignments, args.en_ru_alignments, args.output, args.model, args.count)
        except (ValueError, RuntimeError) as error:
            parser.error(str(error))
        print(f"Qwen audited {count} Bertalign chapters in {args.output}")
    elif args.command == "qwen-align-sequential":
        try:
            count = align_sequential(args.zh, args.en, args.ru, args.output, args.model, args.count)
        except (ValueError, RuntimeError) as error:
            parser.error(str(error))
        print(f"Qwen created {count} coverage-validated chapter alignments in {args.output}")
    elif args.command == "embed-align-pair":
        report = align_pair(args.left, args.right, args.output, args.model_path, device=args.device)
        print(f"BGE-M3 aligned {report['left_paragraphs']} and {report['right_paragraphs']} paragraphs in {args.output}")
    elif args.command == "bertalign-pair":
        from .bertalign_adapter import align_pair as bertalign_pair

        report = bertalign_pair(args.left, args.right, args.output, args.max_align)
        print(f"Bertalign aligned {report['left_paragraphs']} and {report['right_paragraphs']} paragraphs in {args.output}")
    elif args.command == "validate-bge-pair":
        report = validate_pair(args.alignment, args.left, args.right, args.output, args.minimum_similarity)
        print(f"BGE validation status: {report['status']} in {args.output}")
    elif args.command == "review-bge-boundary":
        report = review_boundary(args.validation, args.output, args.model)
        print(f"Qwen boundary review: {report['status']} in {args.output}")
    elif args.command == "monitor":
        from .monitor import launch

        launch()
    elif args.command == "review-audit":
        from .review import launch

        launch(args.audit_dir)
    elif args.command == "ocr-russian":
        try:
            result = ocr_images(args.input, args.output, find_tesseract(args.tesseract), args.psm, preprocess=not args.no_preprocess)
        except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as error:
            parser.error(str(error))
        print(f"OCR completed for {len(result)} pages in {args.output}")
    elif args.command == "assemble-ocr-chapter":
        try:
            result = assemble_ocr_chapter(
                args.pages,
                args.output,
                args.first_stem,
                args.last_stem,
                args.chapter,
                args.title,
                args.printed_pages,
            )
        except ValueError as error:
            parser.error(str(error))
        print(f"OCR chapter draft created: {result}")
    elif args.command == "clean-ocr-chapter":
        result = clean_ocr_chapter_for_review(args.input, args.output, args.report)
        print(f"OCR draft cleaned: {result['lines']} lines; {result['candidates']} review candidates")
    elif args.command == "review-ocr-candidates":
        result = review_ocr_candidates(args.review, args.english, args.output, args.model)
        print(f"OCR candidate review created: {len(result.get('items', []))} items in {args.output}")
    elif args.command == "review-ocr-ui":
        from .ocr_review_ui import launch
        launch(args.qwen_review, args.candidates, args.images)


if __name__ == "__main__":
    main()
