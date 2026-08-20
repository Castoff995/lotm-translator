"""Tkinter review window for Qwen audit findings."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk


class AuditReview(tk.Tk):
    def __init__(self, audit_dir: Path) -> None:
        super().__init__()
        self.audit_dir = audit_dir
        self.title("LOTM Audit Review")
        self.geometry("1180x790")
        self.minsize(900, 600)
        self.items: list[tuple[Path, int, int]] = []
        self.position = 0
        self._load_items()

        self.header = ttk.Label(self, font=("Segoe UI", 11, "bold"))
        self.header.pack(anchor="w", padx=12, pady=(12, 4))
        self.reason = tk.Text(self, height=4, wrap="word", font=("Segoe UI", 10))
        self.reason.pack(fill="x", padx=12)
        self.reason.configure(state="disabled")
        texts = ttk.PanedWindow(self, orient="horizontal")
        texts.pack(fill="both", expand=True, padx=12, pady=10)
        self.panels: dict[str, tk.Text] = {}
        for label in ("Chinese", "Official English", "fan_75 Russian"):
            frame = ttk.Labelframe(texts, text=label)
            widget = tk.Text(frame, wrap="word", font=("Segoe UI", 10))
            widget.pack(fill="both", expand=True, padx=5, pady=5)
            widget.configure(state="disabled")
            texts.add(frame, weight=1)
            self.panels[label] = widget
        comment_header = ttk.Frame(self)
        comment_header.pack(fill="x", padx=12)
        ttk.Label(comment_header, text="Комментарий (необязательно):").pack(side="left")
        ttk.Button(comment_header, text="Вставить из буфера", command=lambda: self.paste_comment(None)).pack(side="right")
        self.comment = tk.Text(self, height=3, wrap="word", font=("Segoe UI", 10))
        self.comment.pack(fill="x", padx=12)
        self.comment.bind("<Control-v>", self.paste_comment)
        self.comment.bind("<Control-V>", self.paste_comment)
        self.comment.bind("<Shift-Insert>", self.paste_comment)
        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=12, pady=10)
        ttk.Button(controls, text="← Назад", command=self.previous).pack(side="left")
        ttk.Button(controls, text="Отклонить", command=lambda: self.decide("rejected")).pack(side="left", padx=8)
        ttk.Button(controls, text="Подтвердить", command=lambda: self.decide("approved")).pack(side="left")
        ttk.Button(controls, text="Позже", command=lambda: self.decide("deferred")).pack(side="left", padx=8)
        ttk.Button(controls, text="Копировать причину", command=lambda: self.copy("reason")).pack(side="left", padx=(18, 4))
        ttk.Button(controls, text="Копировать китайский", command=lambda: self.copy("zh")).pack(side="left", padx=4)
        ttk.Button(controls, text="Копировать английский", command=lambda: self.copy("en")).pack(side="left", padx=4)
        ttk.Button(controls, text="Копировать fan_75", command=lambda: self.copy("ru")).pack(side="left", padx=4)
        ttk.Button(controls, text="Копировать всё", command=lambda: self.copy("all")).pack(side="left", padx=4)
        ttk.Button(controls, text="Пропустить →", command=self.next).pack(side="right")
        self.status = ttk.Label(self)
        self.status.pack(anchor="w", padx=12, pady=(0, 10))
        self.render()

    def _load_items(self) -> None:
        for path in sorted(self.audit_dir.glob("ch_*_audit.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            for window_index, window in enumerate(report.get("windows", [])):
                for finding_index, finding in enumerate(window.get("findings", [])):
                    review = finding.get("review", {}) if isinstance(finding, dict) else {}
                    if not isinstance(review, dict) or review.get("decision") not in {"approved", "rejected"}:
                        self.items.append((path, window_index, finding_index))

    def _current(self) -> tuple[Path, dict[str, object], dict[str, object]] | None:
        if not self.items:
            return None
        path, window_index, finding_index = self.items[self.position]
        report = json.loads(path.read_text(encoding="utf-8"))
        window = report["windows"][window_index]
        finding = window["findings"][finding_index]
        unit_number = finding.get("unit")
        unit = next((value for value in window.get("units", []) if value.get("unit") == unit_number), {})
        return path, finding, unit

    @staticmethod
    def _set_text(widget: tk.Text, value: object) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", str(value or "—"))
        widget.configure(state="disabled")

    def render(self) -> None:
        current = self._current()
        if current is None:
            self.header.config(text="Нет нерассмотренных находок")
            self.status.config(text="Готово")
            return
        path, finding, unit = current
        self.header.config(text=f"{path.name} · кандидат {self.position + 1} из {len(self.items)} · unit {finding.get('unit', '—')}")
        self._set_text(self.reason, f"{finding.get('type', '—')} · {finding.get('severity', '—')}\n{finding.get('reason', '—')}")
        self._set_text(self.panels["Chinese"], unit.get("zh_text"))
        self._set_text(self.panels["Official English"], unit.get("en_text"))
        self._set_text(self.panels["fan_75 Russian"], unit.get("ru_text"))
        self.comment.delete("1.0", "end")
        review = finding.get("review", {})
        if isinstance(review, dict):
            self.comment.insert("1.0", review.get("comment", ""))
        self.status.config(text="Подтвердить — реальная проблема; Отклонить — ложное срабатывание; Позже — оставить на повторную проверку.")

    def decide(self, decision: str) -> None:
        current = self._current()
        if current is None:
            return
        path, _, _ = current
        _, window_index, finding_index = self.items[self.position]
        report = json.loads(path.read_text(encoding="utf-8"))
        finding = report["windows"][window_index]["findings"][finding_index]
        finding["review"] = {"decision": decision, "comment": self.comment.get("1.0", "end").strip()}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.items.pop(self.position)
        if self.position >= len(self.items):
            self.position = max(0, len(self.items) - 1)
        self.render()

    def next(self) -> None:
        if self.items:
            self.position = (self.position + 1) % len(self.items)
            self.render()

    def previous(self) -> None:
        if self.items:
            self.position = (self.position - 1) % len(self.items)
            self.render()

    def copy(self, field: str) -> None:
        current = self._current()
        if current is None:
            return
        _, finding, unit = current
        values = {
            "reason": str(finding.get("reason", "")),
            "zh": str(unit.get("zh_text", "")),
            "en": str(unit.get("en_text", "")),
            "ru": str(unit.get("ru_text", "")),
        }
        values["all"] = (
            f"Причина от Qwen:\n{values['reason']}\n\n"
            f"Китайский:\n{values['zh']}\n\n"
            f"Английский:\n{values['en']}\n\n"
            f"fan_75:\n{values['ru']}"
        )
        self.clipboard_clear()
        self.clipboard_append(values[field])
        self.update()
        self.status.config(text="Скопировано в буфер обмена.")

    def paste_comment(self, _event: object) -> str:
        try:
            self.comment.insert("insert", self.clipboard_get())
            self.status.config(text="Вставлено из буфера обмена.")
        except tk.TclError:
            self.status.config(text="Буфер обмена не содержит текста.")
        return "break"


def launch(audit_dir: Path) -> None:
    AuditReview(audit_dir).mainloop()
