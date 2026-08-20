"""Small Windows-only dashboard for local LOTM pipeline activity."""

from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk


PROJECT_ROOT = Path.cwd().resolve()


def _project_processes() -> list[dict[str, object]]:
    escaped = str(PROJECT_ROOT).replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{escaped}*' }} | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if not result.stdout.strip():
        return []
    parsed = json.loads(result.stdout)
    return parsed if isinstance(parsed, list) else [parsed]


def _progress_files() -> list[tuple[Path, dict[str, object]]]:
    root = PROJECT_ROOT / "data" / "processed"
    reports = []
    for path in root.rglob("progress.json") if root.exists() else []:
        if "history" in path.relative_to(root).parts:
            continue
        try:
            reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(reports, key=lambda item: item[0].stat().st_mtime, reverse=True)


def _recent_files() -> list[Path]:
    root = PROJECT_ROOT / "data" / "processed"
    if not root.exists():
        return []
    files = [
        path for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts and "history" not in path.relative_to(root).parts
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:12]


class Monitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LOTM Monitor — local pipeline")
        self.geometry("1120x720")
        self.minsize(850, 500)
        self.processes = ttk.Treeview(self, columns=("pid", "name", "command"), show="headings", height=8)
        for column, title, width in (("pid", "PID", 75), ("name", "Process", 140), ("command", "Command", 850)):
            self.processes.heading(column, text=title)
            self.processes.column(column, width=width, anchor="w")
        self.jobs = ttk.Treeview(self, columns=("folder", "status", "detail", "updated"), show="headings", height=7)
        for column, title, width in (("folder", "Pipeline folder", 420), ("status", "Status", 140), ("detail", "Progress", 280), ("updated", "Updated", 170)):
            self.jobs.heading(column, text=title)
            self.jobs.column(column, width=width, anchor="w")
        self.files = ttk.Treeview(self, columns=("path", "updated"), show="headings", height=8)
        self.files.heading("path", text="Recent project file")
        self.files.heading("updated", text="Updated")
        self.files.column("path", width=850, anchor="w")
        self.files.column("updated", width=170, anchor="w")
        ttk.Label(self, text="Processes whose command line contains this project folder").pack(anchor="w", padx=10, pady=(10, 0))
        self.processes.pack(fill="x", padx=10)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=6)
        ttk.Button(buttons, text="Refresh now", command=self.refresh).pack(side="left")
        ttk.Button(buttons, text="Stop selected process", command=self.stop_selected).pack(side="left", padx=8)
        ttk.Label(self, text="Progress files").pack(anchor="w", padx=10)
        self.jobs.pack(fill="x", padx=10)
        ttk.Label(self, text="Recently changed files in data/processed").pack(anchor="w", padx=10, pady=(8, 0))
        self.files.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.status = ttk.Label(self, text="")
        self.status.pack(anchor="w", padx=10, pady=(0, 8))
        self.refresh()

    @staticmethod
    def _clear(tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children())

    def refresh(self) -> None:
        self._clear(self.processes)
        try:
            processes = _project_processes()
        except (OSError, json.JSONDecodeError) as error:
            processes = []
            self.status.config(text=f"Cannot query processes: {error}")
        for process in processes:
            self.processes.insert("", "end", values=(process.get("ProcessId"), process.get("Name"), process.get("CommandLine", "")))
        self._clear(self.jobs)
        for path, data in _progress_files():
            detail = " | ".join(f"{key}={value}" for key, value in data.items() if key in {"chapter", "window", "total_windows", "chapters"})
            updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
            self.jobs.insert("", "end", values=(str(path.parent.relative_to(PROJECT_ROOT)), data.get("status", "unknown"), detail, updated))
        self._clear(self.files)
        for path in _recent_files():
            updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
            self.files.insert("", "end", values=(str(path.relative_to(PROJECT_ROOT)), updated))
        self.status.config(text=f"Updated {datetime.now().strftime('%H:%M:%S')} · {len(processes)} project process(es) · auto refresh: 2 sec")
        self.after(2000, self.refresh)

    def stop_selected(self) -> None:
        selected = self.processes.selection()
        if not selected:
            messagebox.showinfo("LOTM Monitor", "Select a project process first.")
            return
        pid = self.processes.item(selected[0], "values")[0]
        if not messagebox.askyesno("Stop process", f"Stop project process PID {pid} and its child processes?"):
            return
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T"], capture_output=True, text=True, check=False)
        self.refresh()


def launch() -> None:
    Monitor().mainloop()
