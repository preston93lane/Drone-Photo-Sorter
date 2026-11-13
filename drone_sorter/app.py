"""Tkinter desktop UI for the drone photo sorter."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .sorter import (
    UNSORTED,
    categories_from_config,
    ensure_config_file,
    load_config,
    sort_photos,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# Palette (same as v5)
BG_DARK = "#1E1E2E"
BG_CARD = "#2A2A3C"
BG_TEXT = "#181825"
FG_LIGHT = "#CDD6F4"
FG_MUTED = "#A6ADC8"
ACCENT_BLUE = "#89B4FA"
ACCENT_GREEN = "#A6E3A1"
ACCENT_RED = "#F38BA8"
ACCENT_YELLOW = "#F9E2AF"

FONT = "Segoe UI"
POLL_MS = 100
MAX_LOG_LINES_PER_POLL = 400


class DroneSorterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"Drone Pitch Sorter v{__version__}")
        root.geometry("760x620")
        root.minsize(620, 480)
        root.configure(bg=BG_DARK, padx=20, pady=20)

        self.source_dirs: list[Path] = []
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.start_time = 0.0

        self.mode = tk.StringVar(value="copy")
        self.dry_run = tk.BooleanVar(value=False)
        self.tolerance = tk.StringVar()

        try:
            self.config_file = ensure_config_file()
        except OSError:
            self.config_file = None
        self.tolerance.set(str(load_config(self.config_file).get("tolerance", 0.5)))

        self._setup_styles()
        self._setup_ui()
        self._log("Ready. Pitch is read directly from each photo, no ExifTool needed.")
        if self.config_file:
            self._log(f"Categories file: {self.config_file}")
        self.root.after(POLL_MS, self._poll_events)

    # ------------------------------------------------------------ layout ---
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=BG_CARD, background=ACCENT_BLUE,
            thickness=8, borderwidth=0,
        )

    def _button(self, parent, text, command, bg=BG_CARD, fg=FG_LIGHT, **kw):
        return tk.Button(
            parent, text=text, command=command, font=(FONT, 9, "bold"),
            bg=bg, fg=fg, activebackground=ACCENT_BLUE, activeforeground=BG_DARK,
            disabledforeground="#585B70", bd=0, padx=15, pady=8, cursor="hand2", **kw,
        )

    def _setup_ui(self):
        tk.Label(
            self.root, text="Gimbal Pitch Photo Sorter",
            font=(FONT, 14, "bold"), bg=BG_DARK, fg=FG_LIGHT,
        ).pack(anchor="w", pady=(0, 10))

        # Drop zone
        drop = tk.Frame(self.root, bg=BG_CARD, highlightbackground=ACCENT_BLUE, highlightthickness=1)
        drop.pack(fill=tk.X, pady=(0, 12), ipady=18)
        text = ("Drag & drop flight folders here\n(or click 'Browse Folder')"
                if HAS_DND else "Click 'Browse Folder' to select a flight folder.")
        tk.Label(drop, text=text, bg=BG_CARD, fg=FG_LIGHT, font=(FONT, 11)).pack(expand=True)
        if HAS_DND:
            drop.drop_target_register(DND_FILES)
            drop.dnd_bind("<<Drop>>", self._handle_drop)

        # Folder row
        row = tk.Frame(self.root, bg=BG_DARK)
        row.pack(fill=tk.X, pady=(0, 10))
        self.btn_browse = self._button(row, "Browse Folder", self._browse)
        self.btn_browse.pack(side=tk.LEFT)
        self.lbl_path = tk.Label(row, text="No folders selected", fg=FG_MUTED, bg=BG_DARK,
                                 font=(FONT, 9, "italic"), anchor="w")
        self.lbl_path.pack(side=tk.LEFT, padx=(15, 0), fill=tk.X, expand=True)

        # Options row
        opts = tk.Frame(self.root, bg=BG_DARK)
        opts.pack(fill=tk.X, pady=(0, 10))
        radio_kw = dict(bg=BG_DARK, fg=FG_LIGHT, selectcolor=BG_CARD, activebackground=BG_DARK,
                        activeforeground=FG_LIGHT, font=(FONT, 9))
        tk.Radiobutton(opts, text="Copy", variable=self.mode, value="copy", **radio_kw).pack(side=tk.LEFT)
        tk.Radiobutton(opts, text="Move", variable=self.mode, value="move", **radio_kw).pack(side=tk.LEFT, padx=(6, 0))
        tk.Checkbutton(opts, text="Dry run (preview only)", variable=self.dry_run, **radio_kw).pack(side=tk.LEFT, padx=(16, 0))
        tk.Label(opts, text="Tolerance ±°", bg=BG_DARK, fg=FG_MUTED, font=(FONT, 9)).pack(side=tk.LEFT, padx=(16, 4))
        tk.Entry(opts, textvariable=self.tolerance, width=5, bg=BG_CARD, fg=FG_LIGHT,
                 insertbackground=FG_LIGHT, bd=0, font=(FONT, 9)).pack(side=tk.LEFT)
        self.btn_config = self._button(opts, "Edit Categories", self._open_config)
        self.btn_config.pack(side=tk.RIGHT)

        # Action row
        act = tk.Frame(self.root, bg=BG_DARK)
        act.pack(fill=tk.X, pady=(0, 10))
        self.btn_start = self._button(act, "Start Sorting", self._start, bg=ACCENT_GREEN, fg=BG_DARK, state=tk.DISABLED)
        self.btn_start.pack(side=tk.RIGHT)
        self.btn_cancel = self._button(act, "Cancel", self._cancel, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.RIGHT, padx=(0, 8))
        self.progress = ttk.Progressbar(act, style="Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(6, 0))
        self.lbl_eta = tk.Label(act, text="", bg=BG_DARK, fg=FG_MUTED, font=(FONT, 9, "bold"), width=22, anchor="e")
        self.lbl_eta.pack(side=tk.LEFT, padx=(10, 10))

        # Log
        self.log_area = scrolledtext.ScrolledText(
            self.root, height=14, state=tk.DISABLED, font=("Consolas", 9),
            bg=BG_TEXT, fg=FG_LIGHT, insertbackground=FG_LIGHT, bd=0,
            highlightbackground=BG_CARD, highlightthickness=1,
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)
        for tag, color in (("ok", ACCENT_GREEN), ("warn", ACCENT_YELLOW), ("err", ACCENT_RED), ("muted", FG_MUTED)):
            self.log_area.tag_config(tag, foreground=color)

    # --------------------------------------------------------------- log ---
    def _log(self, *lines: tuple[str, str] | str):
        """Append lines on the UI thread. Each line is text or (text, tag)."""
        at_bottom = self.log_area.yview()[1] >= 0.98
        self.log_area.config(state=tk.NORMAL)
        for line in lines:
            text, tag = (line, None) if isinstance(line, str) else line
            self.log_area.insert(tk.END, text + "\n", tag or ())
        if at_bottom:
            self.log_area.see(tk.END)
        self.log_area.config(state=tk.DISABLED)

    # ----------------------------------------------------------- folders ---
    def _handle_drop(self, event):
        if self._busy():
            return
        paths = [Path(p) for p in self.root.tk.splitlist(event.data)]
        dirs = [p for p in paths if p.is_dir()]
        if dirs:
            self._set_sources(dirs)
        else:
            self._log(("Dropped items must be folders.", "err"))

    def _browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self._set_sources([Path(folder)])

    def _set_sources(self, dirs: list[Path]):
        self.source_dirs = dirs
        label = dirs[0].name if len(dirs) == 1 else f"{len(dirs)} folders queued"
        self.lbl_path.config(text=label, fg=ACCENT_BLUE)
        self.btn_start.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.lbl_eta.config(text="", fg=FG_MUTED)
        self._log(f"Queued {len(dirs)} folder(s): " + ", ".join(str(d) for d in dirs))

    def _open_config(self):
        if not self.config_file:
            messagebox.showerror("Categories", "Couldn't create categories.json next to the app.")
            return
        path = str(self.config_file)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            messagebox.showerror("Categories", f"Couldn't open {path}\n\n{e}")
            return
        self._log(("Edit and save categories.json; changes apply on the next run.", "muted"))

    # --------------------------------------------------------------- run ---
    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _start(self):
        if not self.source_dirs or self._busy():
            return
        try:
            tol_text = self.tolerance.get().strip()
            tolerance = float(tol_text) if tol_text else None
            categories = categories_from_config(load_config(self.config_file), tolerance)
        except (ValueError, KeyError, TypeError) as e:
            messagebox.showerror("Settings", f"Invalid tolerance or categories.json:\n\n{e}")
            return
        if not categories:
            messagebox.showerror("Settings", "categories.json has no categories.")
            return

        move = self.mode.get() == "move"
        dry = self.dry_run.get()
        if move and not dry and not messagebox.askyesno(
            "Move files?", "Move mode takes the originals out of the flight folders. Continue?"
        ):
            return

        self.cancel_event.clear()
        self._set_running(True)
        self.progress["value"] = 0
        self.start_time = time.monotonic()
        verb = "Previewing" if dry else ("Moving" if move else "Copying")
        self._log("", (f"--- {verb} photos ---", "muted"))

        def work():
            try:
                results = sort_photos(
                    self.source_dirs, categories, move=move, dry_run=dry,
                    on_progress=lambda i, n, r: self.events.put(("progress", i, n, r)),
                    cancel=self.cancel_event,
                )
                self.events.put(("done", results, dry))
            except Exception as e:  # report anything unexpected in the log
                self.events.put(("fatal", e))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _cancel(self):
        self.cancel_event.set()
        self.btn_cancel.config(state=tk.DISABLED)
        self.lbl_eta.config(text="Cancelling...", fg=ACCENT_YELLOW)

    def _set_running(self, running: bool):
        on, off = (tk.DISABLED, tk.NORMAL) if running else (tk.NORMAL, tk.DISABLED)
        self.btn_start.config(state=on)
        self.btn_browse.config(state=on)
        self.btn_config.config(state=on)
        self.btn_cancel.config(state=off)

    def _poll_events(self):
        lines = []
        last_progress = None
        try:
            while len(lines) < MAX_LOG_LINES_PER_POLL:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    _, i, n, r = ev
                    last_progress = (i, n)
                    lines.append(self._format_result(r))
                else:
                    if lines:
                        self._log(*lines)
                        lines = []
                    self._handle_final(ev)
        except queue.Empty:
            pass
        if lines:
            self._log(*lines)
        if last_progress:
            self._update_progress(*last_progress)
        self.root.after(POLL_MS, self._poll_events)

    @staticmethod
    def _format_result(r):
        pitch = f"{r.pitch:6.1f}°" if r.pitch is not None else "   N/A "
        name = f"[{r.source.parent.name}] {r.source.name}"
        if r.action == "error":
            return (f"ERROR  {name}: {r.error}", "err")
        dest = r.dest.name if r.dest and r.dest.name != r.source.name else ""
        extra = f" as {dest}" if dest else ""
        tag = "warn" if r.category == UNSORTED or r.action == "skipped" else None
        return (f"{r.action:8} {pitch}  {name} -> {r.category}/{extra}", tag)

    def _update_progress(self, i: int, n: int):
        self.progress["value"] = (i / n) * 100 if n else 100
        elapsed = time.monotonic() - self.start_time
        if self.cancel_event.is_set() or elapsed < 0.5 or i == 0:
            return
        rate = i / elapsed
        remaining = int((n - i) / rate) if rate else 0
        mins, secs = divmod(remaining, 60)
        eta = f"{mins}m {secs}s" if mins else f"{secs}s"
        self.lbl_eta.config(text=f"{i}/{n} · {rate:.0f}/s · {eta}", fg=FG_LIGHT)

    def _handle_final(self, ev):
        self._set_running(False)
        if ev[0] == "fatal":
            self._log((f"An error occurred: {ev[1]}", "err"))
            self.lbl_eta.config(text="Error", fg=ACCENT_RED)
            return

        _, results, dry = ev
        cancelled = self.cancel_event.is_set()
        if not results:
            self._log(("No photos found to sort.", "warn"))
            self.lbl_eta.config(text="Nothing to do", fg=FG_MUTED)
            return

        elapsed = time.monotonic() - self.start_time
        by_cat = Counter(r.category for r in results)
        by_action = Counter(r.action for r in results)
        title = "Preview" if dry else ("Cancelled" if cancelled else "Complete")
        summary = [("", None), (f"--- {title}: {len(results)} photos in {elapsed:.1f}s ---", "ok")]
        width = max(len(c) for c in by_cat)
        for cat, count in sorted(by_cat.items()):
            summary.append((f"  {cat:<{width}}  {count:>6}", "warn" if cat == UNSORTED else None))
        summary.append(("  " + ", ".join(f"{a}: {c}" for a, c in sorted(by_action.items())), "muted"))
        self._log(*summary)
        self.progress["value"] = 100 if not cancelled else self.progress["value"]
        self.lbl_eta.config(text=title, fg=ACCENT_YELLOW if cancelled else ACCENT_GREEN)


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    DroneSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
