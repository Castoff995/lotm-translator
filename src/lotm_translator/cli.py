from __future__ import annotations

import argparse
import json
from pathlib import Path

from .epub import chapters, extract
from .alignment import build_chapter_alignment
from .ollama import translate_sample
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
    else:
        if args.paragraphs < 1:
            parser.error("--paragraphs must be at least 1")
        translate_sample(args.zh, args.en, args.glossary, args.output, args.model, args.paragraphs)
        print(f"Translation written to {args.output}")


if __name__ == "__main__":
    main()
