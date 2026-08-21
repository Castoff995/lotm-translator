"""Local editor for an English ↔ approved-Russian-group alignment."""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk


def _units(path: Path) -> list[str]:
    return [part.strip() for part in path.read_text(encoding="utf-8").split("\n\n") if part.strip()]


def _parse_numbers(value: str, maximum: int) -> list[int]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    result = [int(part) for part in parts]
    if len(set(result)) != len(result) or any(number < 1 or number > maximum for number in result):
        raise ValueError(f"Номера должны быть уникальными и находиться в диапазоне 1–{maximum}.")
    return result


class AlignmentEditor(tk.Tk):
    def __init__(self, alignment: Path, english: Path, russian_groups: Path) -> None:
        super().__init__()
        self.alignment_path, self.english_path, self.russian_path = alignment, english, russian_groups
        self.title("LOTM — English ↔ Russian Group Editor")
        self.geometry("1400x820")
        self.payload = json.loads(alignment.read_text(encoding="utf-8"))
        self.key = "groups" if "groups" in self.payload else "alignments"
        self.groups: list[dict[str, list[int]]] = self.payload[self.key]
        self.english, self.russian = _units(english), _units(russian_groups)
        self.position = 0

        outer = ttk.Frame(self, padding=10); outer.pack(fill="both", expand=True)
        left = ttk.Frame(outer); left.pack(side="left", fill="y")
        ttk.Label(left, text="Группы", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.listing = tk.Listbox(left, width=31, font=("Segoe UI", 10))
        self.listing.pack(fill="y", expand=True)
        self.listing.bind("<<ListboxSelect>>", self.select_group)
        buttons = ttk.Frame(left); buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="+ Группа", command=self.add_group).pack(side="left")
        ttk.Button(buttons, text="Удалить", command=self.delete_group).pack(side="left", padx=6)

        main = ttk.Frame(outer); main.pack(side="left", fill="both", expand=True, padx=(12, 0))
        mapping = ttk.Frame(main); mapping.pack(fill="x")
        ttk.Label(mapping, text="English абзацы (через запятую):").grid(row=0, column=0, sticky="w")
        self.left_ids = ttk.Entry(mapping, width=32); self.left_ids.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(mapping, text="Русские группы:").grid(row=0, column=2, sticky="w")
        self.right_ids = ttk.Entry(mapping, width=32); self.right_ids.grid(row=0, column=3, sticky="we", padx=6)
        ttk.Button(mapping, text="Применить состав", command=self.apply_group).grid(row=0, column=4, padx=(6, 0))
        mapping.columnconfigure(1, weight=1); mapping.columnconfigure(3, weight=1)

        panes = ttk.Panedwindow(main, orient="horizontal"); panes.pack(fill="both", expand=True, pady=(10, 0))
        self.en_text = self._text_pane(panes, "English: редактируемый производный абзац")
        self.ru_text = self._text_pane(panes, "Русская утверждённая группа: редактируемая производная копия")
        controls = ttk.Frame(main); controls.pack(fill="x", pady=8)
        ttk.Button(controls, text="Сохранить текст этой группы", command=self.save_group_text).pack(side="left")
        ttk.Button(controls, text="Сохранить всё", command=self.save_all).pack(side="left", padx=6)
        ttk.Button(controls, text="Проверить покрытие", command=self.validate).pack(side="left")
        self.status = ttk.Label(main); self.status.pack(anchor="w")
        self.refresh_list(); self.render()

    def _text_pane(self, parent: ttk.Panedwindow, title: str) -> tk.Text:
        frame = ttk.Frame(parent); parent.add(frame, weight=1)
        ttk.Label(frame, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        widget = tk.Text(frame, wrap="word", font=("Segoe UI", 10), undo=True)
        widget.pack(fill="both", expand=True, pady=(5, 0)); return widget

    def refresh_list(self) -> None:
        self.listing.delete(0, "end")
        for index, group in enumerate(self.groups, start=1):
            self.listing.insert("end", f"{index}. EN {group.get('left', [])} ↔ RU {group.get('right', [])}")
        if self.groups:
            self.position = min(self.position, len(self.groups) - 1)
            self.listing.selection_set(self.position); self.listing.see(self.position)

    def select_group(self, _event: object | None = None) -> None:
        chosen = self.listing.curselection()
        if chosen:
            self.position = int(chosen[0]); self.render()

    def render(self) -> None:
        if not self.groups: return
        group = self.groups[self.position]
        left, right = group.get("left", []), group.get("right", [])
        self.left_ids.delete(0, "end"); self.left_ids.insert(0, ", ".join(map(str, left)))
        self.right_ids.delete(0, "end"); self.right_ids.insert(0, ", ".join(map(str, right)))
        self.en_text.delete("1.0", "end"); self.en_text.insert("1.0", "\n\n".join(self.english[i - 1] for i in left))
        self.ru_text.delete("1.0", "end"); self.ru_text.insert("1.0", "\n\n".join(self.russian[i - 1] for i in right))
        self.status.config(text=f"Группа {self.position + 1} из {len(self.groups)}. Изменения применяются кнопками ниже.")

    def apply_group(self) -> None:
        try:
            self.groups[self.position] = {"left": _parse_numbers(self.left_ids.get(), len(self.english)), "right": _parse_numbers(self.right_ids.get(), len(self.russian))}
            self.refresh_list(); self.status.config(text="Состав группы изменён в памяти. Нажми «Сохранить всё».")
        except ValueError as error: self.status.config(text=str(error))

    def save_group_text(self) -> None:
        group = self.groups[self.position]
        for values, text, collection in ((group["left"], self.en_text.get("1.0", "end").strip(), self.english), (group["right"], self.ru_text.get("1.0", "end").strip(), self.russian)):
            parts = [part.strip() for part in text.split("\n\n") if part.strip()]
            if len(parts) != len(values):
                self.status.config(text="Число абзацев в поле должно совпадать с числом номеров группы."); return
            for number, part in zip(values, parts): collection[number - 1] = part
        self.status.config(text="Текст изменён в памяти. Нажми «Сохранить всё».")

    def add_group(self) -> None:
        self.groups.append({"left": [], "right": []}); self.position = len(self.groups) - 1; self.refresh_list(); self.render()

    def delete_group(self) -> None:
        if self.groups: self.groups.pop(self.position); self.position = max(0, self.position - 1); self.refresh_list(); self.render()

    def validate(self) -> bool:
        left = [value for group in self.groups for value in group.get("left", [])]
        right = [value for group in self.groups for value in group.get("right", [])]
        ok = sorted(left) == list(range(1, len(self.english) + 1)) and sorted(right) == list(range(1, len(self.russian) + 1))
        self.status.config(text="Покрытие полное: каждый абзац использован ровно раз." if ok else "Покрытие нарушено: есть пропуски или дубли номеров.")
        return ok

    def save_all(self) -> None:
        if not self.validate(): return
        self.payload[self.key] = self.groups
        self.alignment_path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.english_path.write_text("\n\n".join(self.english) + "\n", encoding="utf-8")
        self.russian_path.write_text("\n\n".join(self.russian) + "\n", encoding="utf-8")
        self.status.config(text="Сохранено. HTML нужно пересобрать отдельной командой после редактирования.")


def launch(alignment: Path, english: Path, russian_groups: Path) -> None:
    AlignmentEditor(alignment, english, russian_groups).mainloop()
