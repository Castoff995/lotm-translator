"""Small local UI for confirming OCR review suggestions."""
from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk


class OcrReview(tk.Tk):
    def __init__(self, qwen_path: Path, candidates_path: Path, images: Path) -> None:
        super().__init__()
        self.qwen_path, self.candidates_path, self.images = qwen_path, candidates_path, images
        self.title("LOTM OCR Review")
        self.geometry("1050x700")
        self.qwen = json.loads(qwen_path.read_text(encoding="utf-8"))
        candidates = json.loads(candidates_path.read_text(encoding="utf-8")).get("candidates", [])
        self.candidates = {index + 1: value for index, value in enumerate(candidates)}
        self.items = [value for value in self.qwen.get("items", []) if isinstance(value, dict)]
        self.pos = 0
        self.header = ttk.Label(self, font=("Segoe UI", 11, "bold")); self.header.pack(anchor="w", padx=12, pady=10)
        self.text = tk.Text(self, wrap="word", font=("Segoe UI", 10)); self.text.pack(fill="both", expand=True, padx=12)
        comment_frame = ttk.Frame(self); comment_frame.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(comment_frame, text="Комментарий:").pack(anchor="w")
        ttk.Button(comment_frame, text="Вставить из буфера", command=lambda: self.paste(self.comment)).pack(anchor="e")
        self.comment = tk.Text(comment_frame, height=3, wrap="word", font=("Segoe UI", 10))
        self.comment.pack(fill="x")
        self.comment.bind("<Control-v>", lambda _event: self.paste(self.comment))
        self.comment.bind("<Control-V>", lambda _event: self.paste(self.comment))
        correction_frame = ttk.Frame(self); correction_frame.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(correction_frame, text="Исправленный текст (необязательно):").pack(anchor="w")
        ttk.Button(correction_frame, text="Вставить из буфера", command=lambda: self.paste(self.correction)).pack(anchor="e")
        self.correction = tk.Text(correction_frame, height=3, wrap="word", font=("Segoe UI", 10))
        self.correction.pack(fill="x")
        self.correction.bind("<Control-v>", lambda _event: self.paste(self.correction))
        self.correction.bind("<Control-V>", lambda _event: self.paste(self.correction))
        controls = ttk.Frame(self); controls.pack(fill="x", padx=12, pady=10)
        ttk.Button(controls, text="Открыть фото", command=self.open_photo).pack(side="left")
        ttk.Button(controls, text="Список проблем", command=self.open_list).pack(side="left", padx=8)
        ttk.Button(controls, text="Копировать OCR", command=lambda: self.copy("ocr")).pack(side="left", padx=8)
        ttk.Button(controls, text="Копировать Qwen", command=lambda: self.copy("qwen")).pack(side="left")
        ttk.Button(controls, text="Подтвердить правку", command=lambda: self.decide("approved")).pack(side="left", padx=8)
        ttk.Button(controls, text="Применить мою правку", command=lambda: self.decide("manual_fix")).pack(side="left", padx=8)
        ttk.Button(controls, text="Оставить как есть", command=lambda: self.decide("leave_as_is")).pack(side="left")
        ttk.Button(controls, text="Позже", command=self.next).pack(side="right")
        self.status = ttk.Label(self); self.status.pack(anchor="w", padx=12, pady=(0, 10))
        self.render()

    def render(self) -> None:
        self.text.delete("1.0", "end")
        if not self.items:
            self.header.config(text="Нет OCR-кандидатов"); return
        item = self.items[self.pos]; candidate = self.candidates.get(item.get("id"), {})
        source = candidate.get("source", {}) if isinstance(candidate, dict) else {}
        decision = item.get("review", {}).get("decision", "не решено") if isinstance(item.get("review", {}), dict) else "не решено"
        self.header.config(text=f"Кандидат {self.pos + 1} из {len(self.items)} · строка {candidate.get('line', '—')} · {source.get('ocr_page', 'фото не найдено')} · {decision}")
        content = (f"OCR:\n{candidate.get('cleaned', '')}\n\nФлаги: {', '.join(candidate.get('reasons', []))}\n\n"
                   f"Qwen: {item.get('verdict', '')}\nПредложение: {item.get('suggested_russian', '') or '—'}\nПричина: {item.get('reason_ru', '')}\n\n"
                   "Сверь с открытым фото. Подтверждение пока лишь сохраняет решение, текст не меняется.")
        self.text.insert("1.0", content)
        self.comment.delete("1.0", "end")
        self.correction.delete("1.0", "end")
        previous = item.get("review", {})
        if isinstance(previous, dict):
            self.comment.insert("1.0", previous.get("comment", ""))
            self.correction.insert("1.0", previous.get("replacement", ""))
        self.status.config(text="Подтвердить — предложение Qwen верно; Оставить как есть — исправление не нужно.")

    def open_list(self) -> None:
        window = tk.Toplevel(self)
        window.title("Список OCR-проблем")
        window.geometry("850x520")
        listing = tk.Listbox(window, font=("Segoe UI", 10))
        listing.pack(fill="both", expand=True, padx=10, pady=10)
        for index, item in enumerate(self.items):
            candidate = self.candidates.get(item.get("id"), {})
            decision = item.get("review", {}).get("decision", "не решено") if isinstance(item.get("review", {}), dict) else "не решено"
            excerpt = str(candidate.get("cleaned", "")).replace("\n", " ")[:85]
            listing.insert("end", f"{index + 1}. [{decision}] строка {candidate.get('line', '—')}: {excerpt}")
        listing.selection_set(self.pos)
        listing.see(self.pos)
        def choose(_event: object | None = None) -> None:
            chosen = listing.curselection()
            if chosen:
                self.pos = int(chosen[0]); self.render(); window.destroy()
        listing.bind("<Double-Button-1>", choose)
        ttk.Button(window, text="Открыть выбранный", command=choose).pack(pady=(0, 10))

    def open_photo(self) -> None:
        candidate = self.candidates.get(self.items[self.pos].get("id"), {})
        page = candidate.get("source", {}).get("ocr_page", "")
        stem = Path(str(page)).stem
        matches = list(self.images.glob(stem + ".*"))
        if matches:
            os.startfile(matches[0])
        else:
            self.status.config(text="Исходное фото не найдено.")

    def copy(self, kind: str) -> None:
        item = self.items[self.pos]; candidate = self.candidates.get(item.get("id"), {})
        value = str(candidate.get("cleaned", "")) if kind == "ocr" else str(item.get("suggested_russian", ""))
        self.clipboard_clear(); self.clipboard_append(value); self.update()
        self.status.config(text="Скопировано в буфер обмена.")

    def paste(self, widget: tk.Text) -> str:
        try:
            widget.insert("insert", self.clipboard_get())
            self.status.config(text="Вставлено из буфера обмена.")
        except tk.TclError:
            self.status.config(text="В буфере нет текста.")
        return "break"

    def decide(self, decision: str) -> None:
        item = self.items[self.pos]
        replacement = self.correction.get("1.0", "end").strip()
        if decision == "manual_fix" and not replacement:
            self.status.config(text="Сначала введи исправленный текст. Введи 111, чтобы удалить всю строку.")
            return
        item["review"] = {"decision": decision, "comment": self.comment.get("1.0", "end").strip(), "replacement": replacement}
        self.qwen_path.write_text(json.dumps(self.qwen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.pos = (self.pos + 1) % len(self.items)
        self.render()

    def next(self) -> None:
        if self.items: self.pos = (self.pos + 1) % len(self.items); self.render()


def launch(qwen_path: Path, candidates_path: Path, images: Path) -> None:
    OcrReview(qwen_path, candidates_path, images).mainloop()
