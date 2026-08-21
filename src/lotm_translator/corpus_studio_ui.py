"""First tab of the local LOTM Corpus Studio: Sources and OCR."""
from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


class CorpusStudio(tk.Tk):
    """A small shell around the existing local corpus tools.

    The files remain the source of truth.  This UI only discovers them and
    opens the appropriate review tools; it does not hide or duplicate data.
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root.resolve()
        self.preferences_path = self.root / "data" / "processed" / "corpus_studio_preferences.json"
        self.preferences = self._load_preferences()
        self.title("LOTM Corpus Studio")
        self.geometry(str(self.preferences.get("window_geometry", "1120x690")))
        self.minsize(900, 560)
        self._build()
        self.refresh()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _load_preferences(self) -> dict[str, object]:
        try:
            payload = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _close(self) -> None:
        widths = {column: self.table.column(column, "width") for column in self.table["columns"]}
        payload = {"window_geometry": self.geometry(), "table_column_widths": widths}
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            self.preferences_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass  # Layout is a convenience; it must never block closing the app.
        self.destroy()

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        sources = ttk.Frame(notebook, padding=12)
        notebook.add(sources, text="Источники и OCR")

        ttk.Label(sources, text="Корпус: источники и официальный русский OCR", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(
            sources,
            text="Китайско-английская линия расширена до пяти стадий; fan_75, OCR и аудит остаются на своих местах.",
        ).pack(anchor="w", pady=(2, 10))

        columns = ("chapter", "zh_source", "zh_units", "en_source", "en_units", "alignment", "fan", "ocr", "audit", "next")
        table_box = ttk.Frame(sources)
        table_box.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_box, columns=columns, show="headings", height=12, selectmode="browse")
        headings = {
            "chapter": ("Глава", 65), "zh_source": ("1. Кит. источник", 190),
            "zh_units": ("2. Кит. разбивка", 190), "en_source": ("3. Англ. источник", 190),
            "en_units": ("4. Англ. разбивка", 190), "alignment": ("5. Группы кит↔англ", 210),
            "fan": ("fan_75", 90), "ocr": ("Офф. OCR", 105), "audit": ("Аудит OCR", 115),
            "next": ("Следующее действие", 330),
        }
        for key, (label, width) in headings.items():
            self.table.heading(key, text=label)
            saved_width = self.preferences.get("table_column_widths", {}).get(key, width)
            self.table.column(key, width=saved_width if isinstance(saved_width, int) else width, anchor="center")
        horizontal = ttk.Scrollbar(table_box, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=horizontal.set)
        self.table.pack(fill="both", expand=True)
        horizontal.pack(fill="x")
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._show_selected())

        details = ttk.LabelFrame(sources, text="Выбранная глава", padding=10)
        details.pack(fill="x", pady=(10, 0))
        self.details = ttk.Label(details, text="Выбери главу в таблице.", justify="left")
        self.details.pack(anchor="w")

        buttons = ttk.Frame(sources)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Обновить", command=self.refresh).pack(side="left")
        ttk.Button(buttons, text="Открыть папку фото", command=self.open_photos).pack(side="left", padx=6)
        ttk.Button(buttons, text="Открыть OCR-текст", command=self.open_ocr_text).pack(side="left")
        ttk.Button(buttons, text="Проверить OCR", command=self.open_ocr_review).pack(side="left", padx=6)
        ttk.Button(buttons, text="Открыть папку корпуса", command=lambda: self._open(self.root)).pack(side="right")

        future = ttk.Frame(notebook, padding=18)
        notebook.add(future, text="Дальше")
        ttk.Label(future, text="Следующие вкладки появятся после утверждения каркаса:", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            future,
            text="Разбивка абзацев · Связки переводов · Аудит связок · Аудит OCR\n\n"
                 "Пока они намеренно не реализованы: сначала доводим один этап — источники и OCR.",
            justify="left",
        ).pack(anchor="w", pady=10)

    def _paths(self, chapter: int) -> dict[str, Path]:
        ident = f"{chapter:04d}"
        raw = self.root / "data" / "raw"
        return {
            "zh_dir": raw / "qwen_10" / "zh",
            "en_dir": raw / "qwen_10" / "en",
            "fan": raw / "qwen_10" / "ru_fan_75" / f"ch_{ident}_ru_fan_75.txt",
            "ocr_base": raw / "official_ru_apple" / "chapters" / f"ch_{ident}_ru_official_apple",
            "photos": self.root / "lotm" / "ocr_input" / f"chapter{ident}",
        }

    @staticmethod
    def _first(directory: Path, pattern: str) -> Path | None:
        return next(iter(sorted(directory.glob(pattern))), None) if directory.is_dir() else None

    def _row(self, chapter: int) -> tuple[tuple[str, ...], dict[str, Path | None]]:
        paths = self._paths(chapter)
        zh = self._first(paths["zh_dir"], f"{chapter:04d}_*.txt")
        en = self._first(paths["en_dir"], f"{chapter + 4:04d}_*.txt")
        base = paths["ocr_base"]
        reviewed = base.with_name(base.name + "_reviewed.txt")
        audit = base.with_name(base.name + "_qwen_review.json")
        resolved = total = 0
        if audit.is_file():
            try:
                items = json.loads(audit.read_text(encoding="utf-8")).get("items", [])
                total = len(items)
                resolved = sum(bool(item.get("review", {}).get("decision")) for item in items if isinstance(item, dict))
            except (OSError, json.JSONDecodeError):
                total = -1
        base_corpus = self.root / "data" / "corpus"
        ident = f"ch_{chapter:04d}"
        stages = (
            base_corpus / "01_zh_source" / f"{ident}_zh_source.txt",
            base_corpus / "02_zh_units" / f"{ident}_zh_units.txt",
            base_corpus / "03_en_source" / f"{ident}_en_source.txt",
            base_corpus / "04_en_units" / f"{ident}_en_units.txt",
            base_corpus / "05_zh_en_alignment" / f"{ident}_zh_en_alignment.json",
        )
        gold = self._gold_paths()
        def state(path: Path) -> str:
            if not path.is_file():
                return "—"
            return "эталон" if path.relative_to(self.root).as_posix() in gold else "готово"
        ready = reviewed.is_file()
        if not all((zh, en, paths["fan"].is_file())):
            next_step = "Добавить исходники"
        elif not ready:
            next_step = "Собрать / проверить OCR"
        else:
            next_step = "OCR готов; следующий этап — разбивка"
        values = (
            str(chapter), *(state(path) for path in stages), "есть" if paths["fan"].is_file() else "—",
            "готов" if ready else "нет", f"{resolved}/{total}" if total >= 0 else "ошибка JSON", next_step,
        )
        return values, {"zh": zh, "en": en, "fan": paths["fan"], "reviewed": reviewed, "audit": audit, "photos": paths["photos"]}

    def _gold_paths(self) -> set[str]:
        path = self.root / "config" / "gold_alignment_references.json"
        if not path.is_file():
            return set()
        try:
            values = json.loads(path.read_text(encoding="utf-8")).values()
        except (OSError, json.JSONDecodeError):
            return set()
        result: set[str] = set()
        for item in values:
            if isinstance(item, dict):
                for key in ("output", "alignment"):
                    value = item.get(key)
                    if isinstance(value, str):
                        result.add(Path(value).as_posix())
        return result

    def refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        self.rows: dict[str, dict[str, Path | None]] = {}
        for chapter in range(1, 11):
            values, paths = self._row(chapter)
            item = self.table.insert("", "end", values=values)
            self.rows[item] = paths
        first = self.table.get_children()
        if first:
            self.table.selection_set(first[0])
            self._show_selected()

    def _selected(self) -> tuple[int, dict[str, Path | None]] | None:
        selected = self.table.selection()
        if not selected:
            return None
        item = selected[0]
        return int(self.table.item(item, "values")[0]), self.rows[item]

    def _show_selected(self) -> None:
        value = self._selected()
        if not value:
            return
        chapter, paths = value
        photo_state = "есть" if paths["photos"] and paths["photos"].is_dir() else "не найдена"
        review_state = "есть" if paths["audit"] and paths["audit"].is_file() else "ещё не создан"
        ocr_state = "готов" if paths["reviewed"] and paths["reviewed"].is_file() else "ещё нет"
        self.details.config(text=(f"Глава {chapter:04d}. Русские источники: fan_75 — {'есть' if paths['fan'] and paths['fan'].is_file() else 'нет'}; "
                                  f"официальный OCR — {ocr_state}; фото — {photo_state}; аудит OCR — {review_state}.\n"
                                  "«эталон» в таблице означает: файл вручную проверен и зарегистрирован в реестре эталонов."))

    def _open(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo("LOTM Corpus Studio", f"Пока нет: {path}")
            return
        os.startfile(path)  # Windows-only project.

    def open_photos(self) -> None:
        value = self._selected()
        if value:
            self._open(value[1]["photos"] or self.root / "lotm" / "ocr_input")

    def open_ocr_text(self) -> None:
        value = self._selected()
        if value:
            self._open(value[1]["reviewed"] or self.root)

    def open_ocr_review(self) -> None:
        value = self._selected()
        if not value:
            return
        _chapter, paths = value
        review, base = paths["audit"], self._paths(value[0])["ocr_base"]
        candidates = base.with_name(base.name + "_review.json")
        if not review or not review.is_file() or not candidates.is_file():
            messagebox.showinfo("LOTM Corpus Studio", "Для этой главы ещё нет готовых файлов аудита OCR.")
            return
        from .ocr_review_ui import launch
        self.withdraw()
        try:
            launch(review, candidates, self.root / "lotm" / "ocr_input")
        finally:
            self.deiconify()


def launch(root: Path = Path.cwd()) -> None:
    CorpusStudio(root).mainloop()
