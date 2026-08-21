"""Create stable one-sentence units from a cleaned Russian chapter."""
from __future__ import annotations

import re
from pathlib import Path


_SENTENCE = re.compile(r".*?(?:[.!?…]+(?:[»”\"]+)?)(?=\s|$)", re.DOTALL)
_ELLIPSIS_CONTINUATION = re.compile(r"…(?=\s+[а-яё])")
_QUOTE_NAME_ELLIPSIS = re.compile(
    r"(«[^»]*?)…(?=\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?[?!][»”\"])")
_ELLIPSIS_SHORT_QUERY = re.compile(r"(…\s*[А-Яа-яЁё]{1,4})\?\s+(?=[А-ЯЁ])")
_INCOMPLETE_ELLIPSIS = re.compile(r"(?:^|[«\s])(?:Хотя|Но|Или|Впрочем|Зато)…[»”\"]?$")
_QUOTE_WITH_AUTHOR_ASIDE = re.compile(
    r"(«[^»]*?)…\s+—\s+([^.!?…]+[.!?])\s+—\s+"
)
_QUOTED_THOUGHT_WITH_ASIDE = re.compile(
    r"(«(?:(?!»).)*?—\s+[А-ЯЁ][^.!?…]{0,160}[.!?…]\s+—\s+(?:(?!»).)*?)([.!?…]+»)"
)
_QUOTED_TO_LOWER_AUTHOR = re.compile(r"(«[^»]*»\s+—\s+[а-яё][^.!?…]{0,300})([.!?])")
_QUOTED_TO_CAPITAL_AUTHOR = re.compile(r"(«[^»]*»\s+—\s+[А-ЯЁ][^.!?…]{0,300})([.!?])")
_SIMPLE_QUOTE = re.compile(r"(«[^«»]*)([.!?…]+»)" )
_DESCRIPTION_ELLIPSIS_CONTINUATION = re.compile(r"([^.!?…]{0,500}:\s+[^.!?…]{1,500})…(?=\s+[А-ЯЁ])")
_RITUAL_INVOCATION_BREAK = re.compile(
    r"((?:Сделав\s+первый\s+шаг|На\s+второй\s+шаг|Он\s+ступил\s+третий\s+шаг|"
    r"На\s+четвертый\s+шаг)[^.!?…]*?):\s+(—\s+Да\s+снизойдет\s+благословение)"
)
_SPEECH_CUE_BREAK = re.compile(
    r"((?:[А-ЯЁ][^.!?…]{0,360}\b(?:выпалили|воскликнули|крикнули|хором\s+сказали))):\s+(—)"
)
_DIALOGUE_TO_LOWER_AUTHOR = re.compile(r"(—\s+(?:(?!\s+—\s+|␤).){1,600})\s+—\s+(?=[а-яё])")
_DIALOGUE_TO_NAMED_AUTHOR = re.compile(
    r"(—\s+(?:(?!\s+—\s+|␤).){1,600})\s+—\s+"
    r"(?=[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\s+(?:сказал|спросил|ответил|"
    r"пробормотал|услышал|подумал|усмехнулся|вздохнул|кивнул))"
)
_DIALOGUE_ELLIPSIS_TO_CAPITAL = re.compile(r"(—\s+[^.!?…]{0,240})…(?=\s+[А-ЯЁ])")
_SHORT_DIALOGUE_CONTINUATION = re.compile(
    r"(—\s+[^.!?…]{1,120}[!?])\s+([А-ЯЁ][^—.!?…]{1,160}[.!?])"
)
_DIALOGUE_WITH_AUTHOR_ASIDE = re.compile(
    r"(—\s+[^.!?…]{0,120}[!?]\s+(?:[А-ЯЁ][^.!?…]{0,220}[!?]\s+)?"
    r"—\s+[а-яё][^.!?…]{0,220}[.!?]\s+—\s+[^.!?…]{0,220})([.!?])"
)
_PROTECTED_PUNCTUATION = str.maketrans({".": "␠", "!": "␡", "?": "␢", "…": "␣"})


def _restore_protected_marks(text: str) -> str:
    return (
        text.replace("␟", " ").replace("␞", "…").replace("␝", "?")
        .replace("␠", ".").replace("␡", "!").replace("␢", "?").replace("␣", "…")
        .replace("␤", " ")
    )


def _protect_dialogue_with_author_aside(match: re.Match[str]) -> str:
    """Keep a spoken line, its author aside and resumed speech together."""
    body, ending = match.groups()
    return body.translate(_PROTECTED_PUNCTUATION) + ending


def _protect_dialogue_prefix(match: re.Match[str]) -> str:
    """Hide internal punctuation before an OCR dialogue attribution dash."""
    return match.group(1).translate(_PROTECTED_PUNCTUATION) + " — "


def _protect_short_dialogue_continuation(match: re.Match[str]) -> str:
    # In a ritual, the following «На второй/четвёртый шаг...» is a new
    # action+formula unit, not the continuation of the preceding invocation.
    if re.match(r"(?:На\s+(?:второй|четвертый)\s+шаг|Он\s+ступил)", match.group(2)):
        return match.group(1) + " " + match.group(2)
    return match.group(1).translate(_PROTECTED_PUNCTUATION) + " " + match.group(2)


def _protect_quoted_thought_with_aside(match: re.Match[str]) -> str:
    return match.group(1).translate(_PROTECTED_PUNCTUATION) + match.group(2)


def _protect_quoted_to_lower_author(match: re.Match[str]) -> str:
    return match.group(1).translate(_PROTECTED_PUNCTUATION) + match.group(2)


def _protect_simple_quote(match: re.Match[str]) -> str:
    return match.group(1).translate(_PROTECTED_PUNCTUATION) + match.group(2)


def _protect_fan75_source_paragraphs(text: str, source: Path) -> str:
    """Keep fan_75's intentional dialogue/thought paragraph units intact.

    Fan_75 already gives meaningful paragraph boundaries for dialogue, internal
    monologue and card descriptions.  Unlike OCR, their internal full stops
    do not automatically create a new corpus unit.
    """
    if "fan_75" not in source.as_posix().lower():
        return text
    protected: list[str] = []
    for line in text.replace("...", "…").splitlines():
        stripped = line.strip()
        starts_structured_paragraph = stripped.startswith(("—", "-", "«", "На карте"))
        ending = re.search(r"([.!?…]+[»”\"]?)\s*$", stripped)
        if starts_structured_paragraph and ending:
            body = stripped[:ending.start()]
            protected.append(body.translate(_PROTECTED_PUNCTUATION) + ending.group(1))
        else:
            protected.append(line)
    return "\n".join(protected)


def _protect_official_source_paragraphs(text: str, source: Path) -> str:
    """Preserve official translation paragraphs after joining OCR line wraps."""
    if "official_ru_apple" not in source.as_posix().lower():
        return text
    lines = text.replace("...", "…").splitlines()
    protected: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        # For «Дрессировщица?..» — Глаза ... the quote and only the first
        # following author sentence are handled by a narrower regex below.
        has_capital_author_after_quote = bool(re.search(r"»\s*[-—]\s+[А-ЯЁ]", stripped))
        structured = stripped.startswith(("—", "-", "«")) and not has_capital_author_after_quote
        if not structured:
            protected.append(lines[index])
            index += 1
            continue
        paragraph = stripped
        paragraph = re.sub(r"\s+-\s+(?=[А-Яа-яЁё])", " — ", paragraph)
        ending = re.search(r"([.!?…]+[»”\"]?)\s*$", paragraph)
        while not ending and index + 1 < len(lines):
            index += 1
            paragraph += " " + lines[index].strip()
            ending = re.search(r"([.!?…]+[»”\"]?)\s*$", paragraph)
        if ending:
            body = paragraph[:ending.start()]
            protected.append(body.translate(_PROTECTED_PUNCTUATION) + ending.group(1))
        else:
            protected.append(paragraph)
        index += 1
    return "\n".join(protected)


def split_russian_sentences(source: Path, output: Path) -> int:
    text = source.read_text(encoding="utf-8")
    # Chapter number and title are metadata, not a corpus sentence. The first
    # prose line can start immediately afterwards with dialogue.
    text = re.sub(r"^\s*глава\s+\d+\.?\s*[^\n]*\n+", "", text, count=1, flags=re.IGNORECASE)
    # MirNovel's page footer is sometimes included after the chapter text.
    # It begins with a long underscore separator and has no prose value.
    text = re.sub(r"\n_{8,}\s*\n(?=[\s\S]{0,800}\bПоддержать автора\b)[\s\S]*$", "", text)
    # Confirmed translator notes in official chapter 5 are not narrative.
    text = re.sub(
        r"(?ms)^'?\s*Один из пяти богов древних китайских легенд.*?Прим\.\s*пер\.?\s*\n"
        r"2\s+Верховное божество.*?Прим\.\s*пер\s*\n?",
        "",
        text,
    )
    # Fan translations use three asterisks as a scene divider.  The glyphs
    # are not prose and must not enter the corpus; the blank-line boundary is.
    text = re.sub(r"(?m)^\s*\*{3,}\s*$", "\n\n", text)
    text = re.sub(r"\*{3,}(?=\s*[.!?…»”\"])", "", text)
    text = _protect_fan75_source_paragraphs(text, source)
    text = _protect_official_source_paragraphs(text, source)
    if "fan_75" in source.as_posix().lower():
        # Fan_75's blank lines are real author paragraph boundaries.  Keep a
        # visible separator through whitespace normalization; a preceding
        # colon still lets «авторские слова: → реплика» form one unit.
        text = re.sub(r"\n[ \t]*\n+", " ␤ ", text)
    # Apple OCR frequently emits a simple hyphen where a dialogue dash stood
    # at the start of its line.  It is structural punctuation, not a minus.
    text = re.sub(r"(?m)^\s*-\s+", "— ", text)
    text = re.sub(r'([»”"]+)\s+-\s+(?=[А-Яа-яЁё])', r"\1 — ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # A standalone OCR hyphen after completed speech is normally the author
    # remark dash. Hyphens inside words and numeric ranges are untouched.
    text = re.sub(r"([.!?…])\s+-\s+(?=[а-яё])", r"\1 — ", text)
    # OCR writes a semantic ellipsis both as U+2026 and as three full stops.
    text = text.replace("...", "…")
    # A startled interjection can be part of the same thought: «…А? Боль
    # уже…».  Its question mark is internal; the final ellipsis closes it.
    text = _ELLIPSIS_SHORT_QUERY.sub(r"\1␝ ", text)
    # A name can begin with a capital letter inside a broken-off quotation:
    # «Теперь я… Клейн Моретти?».  This is not a sentence boundary.
    text = _QUOTE_NAME_ELLIPSIS.sub(r"\1␞", text)
    # An ellipsis inside a sentence is not its end: «Неужели я… перенесся…».
    text = _ELLIPSIS_CONTINUATION.sub("␞", text)
    if "official_ru_apple" in source.as_posix().lower():
        # A colon-led visual enumeration can continue after its ellipsis:
        # «...звёздочки: большие, яркие… Некоторые светились вдали...».
        text = _DESCRIPTION_ELLIPSIS_CONTINUATION.sub(r"\1␞", text)
        # The ritual's action is a visual lead-in; its invocation begins a
        # distinct paragraph after the colon.
        text = _RITUAL_INVOCATION_BREAK.sub(r"\1:␥\2", text)
        text = _SPEECH_CUE_BREAK.sub(r"\1:␥\2", text)
        # Official internal monologues keep their own author paragraph.
        text = _SIMPLE_QUOTE.sub(_protect_simple_quote, text)
    # A single internal monologue may contain an author aside and several
    # short sentences; its closing quote determines the paragraph boundary.
    text = _QUOTED_THOUGHT_WITH_ASIDE.sub(_protect_quoted_thought_with_aside, text)
    text = _QUOTED_TO_LOWER_AUTHOR.sub(_protect_quoted_to_lower_author, text)
    text = _QUOTED_TO_CAPITAL_AUTHOR.sub(_protect_quoted_to_lower_author, text)
    # A quotation may contain an author aside: «Таро… — Чжоу замер. — ...!».
    text = _QUOTE_WITH_AUTHOR_ASIDE.sub(r"\1␞ — \2␟— ", text)
    # In OCR, a whole spoken phrase can contain several question marks before
    # its author attribution: «— Девять? ...? — спросил он».  Keep it whole.
    text = _DIALOGUE_TO_LOWER_AUTHOR.sub(_protect_dialogue_prefix, text)
    text = _DIALOGUE_TO_NAMED_AUTHOR.sub(_protect_dialogue_prefix, text)
    # A resumed phrase can start with a capital after an internal ellipsis:
    # «— Если, ну... Цена подходящая, то попробую».
    text = _DIALOGUE_ELLIPSIS_TO_CAPITAL.sub(r"\1␞", text)
    # Market cries and similar short uninterrupted calls stay one paragraph:
    # «— Подходите! Ароматная жареная рыбка!».
    text = _SHORT_DIALOGUE_CONTINUATION.sub(_protect_short_dialogue_continuation, text)
    # The same construction without quotes remains a single paragraph:
    # «— 9? А два дня назад? — спросил он. — А ещё раньше...».
    text = _DIALOGUE_WITH_AUTHOR_ASIDE.sub(_protect_dialogue_with_author_aside, text)
    # In Russian dialogue, «… — сказал он» is one sentence. Protect its
    # internal sentence mark from the generic splitter.
    # Keep an author's attribution attached to a *closed quotation*.
    # A bare ``! — Кх…`` is instead a new spoken sentence and must split.
    text = re.sub(r"([.!?…]+[»”\"])\s+—\s+(?=[А-Яа-яЁё])", r"\1␟— ", text)
    # Likewise, a short interjection followed by a lowercase author action is
    # one dialogue sentence: ``— Кх… — в оцепенении он…``.
    text = re.sub(
        r"(—\s+[^—.!?…]{1,40}[.!?…])\s+—\s+(?=[а-яё])",
        r"\1␟— ",
        text,
    )
    matches = list(_SENTENCE.finditer(text))
    units = [_restore_protected_marks(match.group(0)).strip() for match in matches if match.group(0).strip()]
    remainder = text[matches[-1].end():].strip() if matches else text
    if remainder:
        units.append(_restore_protected_marks(remainder))

    # A protected structural break can divide a unit even where the preceding
    # lead-in ends with a colon rather than terminal punctuation.
    units = [part.strip() for unit in units for part in unit.split("␥") if part.strip()]

    # A standalone unfinished connector (e.g. «Хотя…») continues the next
    # phrase. A standalone reaction such as «Что…» remains its own unit.
    merged: list[str] = []
    for unit in units:
        if merged and _INCOMPLETE_ELLIPSIS.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {unit}"
        else:
            merged.append(unit)
    units = merged
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(units) + "\n", encoding="utf-8")
    return len(units)


def split_russian_with_razdel_rules(source: Path, output: Path) -> int:
    """Use Razdel for Russian sentence boundaries, then apply project exceptions."""
    try:
        from razdel import sentenize
    except ImportError as error:  # pragma: no cover - installation is explicit
        raise RuntimeError("Razdel is not installed. Run: .\\.venv\\Scripts\\python.exe -m pip install razdel") from error

    text = source.read_text(encoding="utf-8")
    text = re.sub(r"^\s*глава\s+\d+\.?\s*[^\n]*\n+", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"\n_{8,}\s*\n(?:Комната|Поддержать автора)\b[\s\S]*$", "", text).strip()
    raw_units = [item.text.strip().replace("\n", " ") for item in sentenize(text) if item.text.strip()]
    units: list[str] = []
    # Razdel can attach a startled «…А?» to the preceding finished command.
    # Split that attachment so the project rule can join it with its real
    # continuation instead.
    for unit in raw_units:
        match = re.fullmatch(r"(.+[.!?])\s+(…\s*[А-Яа-яЁё]{1,4}\?)", unit)
        if match:
            units.extend([match.group(1), match.group(2)])
        else:
            units.append(unit)

    merged: list[str] = []
    index = 0
    while index < len(units):
        current = units[index]
        while index + 1 < len(units):
            following = units[index + 1]
            unfinished_quote = current.count("«") > current.count("»") and current.rstrip().endswith("…")
            short_ellipsis_question = bool(re.fullmatch(r"…\s*[А-Яа-яЁё]{1,4}\?", current))
            merge = (
                unfinished_quote
                or short_ellipsis_question
                or bool(_INCOMPLETE_ELLIPSIS.search(current))
                or (current.rstrip().endswith("…") and following.lstrip().startswith("—"))
                or (current.rstrip().endswith(("…»", "…”", "…\"")) and following.lstrip().startswith("—"))
            )
            if not merge:
                break
            current += " " + following
            index += 1
        merged.append(current)
        index += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(merged) + "\n", encoding="utf-8")
    return len(merged)
