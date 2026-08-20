# LOTM local translator

Local, privacy-preserving toolchain for preparing a Chinese-to-Russian
translation corpus for *Lord of the Mysteries*. It is deliberately split into
two stages:

1. extract and align licensed source material locally;
2. use the resulting dataset with a local Qwen model and a LoRA adapter.

The source EPUBs are not copied into Git. Put them in `lotm/` (in this
workspace it is a junction to `C:\Users\250fps\Desktop\lotm`). Derived texts
also stay ignored by Git.

## First run

Python 3.11+ is sufficient for the current extraction stage; it uses only the
standard library.

```powershell
python -m src.lotm_translator.cli inspect ".\lotm\诡秘之主.epub"
python -m src.lotm_translator.cli inspect ".\lotm\Lord.of.the.Mysteries.-.Webnovel.Default.epub"

python -m src.lotm_translator.cli extract ".\lotm\诡秘之主.epub" ".\data\raw\initial\zh" --language zh --count 4
python -m src.lotm_translator.cli extract ".\lotm\Lord.of.the.Mysteries.-.Webnovel.Default.epub" ".\data\raw\initial\en" --language en --start 5 --count 4
```

Each extraction creates numbered `.txt` chapters and `manifest.json`. `--start`
is the position in the EPUB table of contents, not necessarily the visible
chapter number. Review the two manifests before aligning chapters: prefaces,
tables of contents, and bonus chapters may differ between editions.

## Source policy

Fan translations are imported as a separately labelled source and never treated
as the final authority. Inline footnote references such as `[1]` are removed
when text enters the training corpus. We do not generate, retain, or train on
footnote explanations unless this policy is deliberately changed later.

Translation decisions are versioned in `config/translation_rules.md`; approved
names and terms are stored in `config/glossary.json`.
Known chapter catalogues are recorded in `config/sources.json`.

## Current corpus state

The initial four chapters are matched at chapter level in
`data/processed/chapter_alignment.json`. The next stage is paragraph alignment
with a review queue; it will use the Chinese original and official English as
the primary anchors. Until the official Russian translation arrives, `fan_75`
is only a lower-weight style/reference signal.

## Local Ollama smoke test

With Ollama running locally, the following sends a short sample only to
`127.0.0.1:11434` and writes a Russian draft outside Git:

```powershell
python -m src.lotm_translator.cli translate-sample `
  --zh ".\data\raw\initial\zh\0001_第一章 绯红.txt" `
  --en ".\data\raw\initial\en\0005_Chapter 1_ Crimson.txt" `
  --output ".\data\processed\smoke_test_ru.txt" `
  --model qwen3:14b `
  --paragraphs 5
```

Qwen 3 reasoning is disabled for translation requests: it is useful for
analysis, but needlessly slows a deterministic literary translation.

## Local Russian OCR

The OCR stage works entirely on the local computer and expects one clean scan
per image file. Install Tesseract OCR for Windows with Russian (`rus`) and
English (`eng`) language data, then place iPhone scans in
`official_ru_scans/`.

```powershell
python -m src.lotm_translator.cli ocr-russian `
  ".\official_ru_scans" `
  ".\data\raw\official_ru_ocr"
```

The tool accepts JPG, PNG, TIFF and WEBP. It writes one UTF-8 `.txt` file per
page, removes soft hyphens created by line wrapping, and does not use an LLM to
fill missing text. Review output against scans before treating it as an
official reference.

## Planned pipeline

`EPUB → chapter extraction → manual/assisted alignment → terminology glossary →
training JSONL → Qwen LoRA → local Ollama inference`

Only material you are entitled to use should be placed in the local corpus.
