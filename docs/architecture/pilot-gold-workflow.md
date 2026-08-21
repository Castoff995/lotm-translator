# Pilot Gold Workflow

Run the foundation CLI from the repository root. The example uses chapter 1
and three already extracted UTF-8 chapter files. Ingest copies them into
immutable v2 raw storage; it never edits the supplied files.

```powershell
$python = ".\.venv\Scripts\python.exe"

& $python -m src.lotm_v2.cli manifest-init data\manifests\v2\zh.json --source-id zh --language zh --role original --edition "Chinese EPUB pilot" --format text
& $python -m src.lotm_v2.cli manifest-init data\manifests\v2\en.json --source-id en --language en --role official --edition "Official English pilot" --format text
& $python -m src.lotm_v2.cli manifest-init data\manifests\v2\ru-official.json --source-id ru-official --language ru --role official --edition "Official Russian pilot" --format ocr --paragraphization-mode manual_spans

& $python -m src.lotm_v2.cli ingest-text data\manifests\v2\zh.json ".\path\to\ZH_CHAPTER_1.txt" --chapter 1
& $python -m src.lotm_v2.cli ingest-text data\manifests\v2\en.json ".\path\to\EN_CHAPTER_1.txt" --chapter 1
& $python -m src.lotm_v2.cli ingest-text data\manifests\v2\ru-official.json ".\path\to\RU_CHAPTER_1.txt" --chapter 1

# Create and review a versioned manual-spans JSON artifact from the Russian
# source/photos, then validate and freeze its exact hash in the manifest.
& $python -m src.lotm_v2.cli paragraphization-check data\manifests\v2\ru-official.json data\manifests\v2\paragraphization\ru-official\ch_0001.json --chapter 1
& $python -m src.lotm_v2.cli paragraphization-register data\manifests\v2\ru-official.json data\manifests\v2\paragraphization\ru-official\ch_0001.json --chapter 1

& $python -m src.lotm_v2.cli normalize data\manifests\v2\zh.json --chapter 1
& $python -m src.lotm_v2.cli normalize data\manifests\v2\en.json --chapter 1
& $python -m src.lotm_v2.cli normalize data\manifests\v2\ru-official.json --chapter 1

& $python -m src.lotm_v2.cli gold-draft data\normalized\v2\zh\ch_0001.json data\normalized\v2\en\ch_0001.json data\normalized\v2\ru-official\ch_0001.json
```

Edit `data/gold/v2/ch_0001.json` manually. Populate `alignment_units` with
stable paragraph IDs, use structured GAP objects when a side has no
counterpart, add explicit `paragraph_dispositions` for reviewed physical
paragraphs intentionally excluded from translation alignment, and add one
`JOIN` or `BREAK` entry after every unit except the last. Every physical
paragraph must have exactly one fate: AlignmentUnit XOR ParagraphDisposition.
Keep `status` as `draft` until human review is complete.

```powershell
& $python -m src.lotm_v2.cli gold-validate data\gold\v2\ch_0001.json
```

Repeat for chapter 2. Draft generation never infers alignment and never marks a
chapter `confirmed`.
