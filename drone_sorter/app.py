"""Desktop UI for the drone photo sorter (Windows 11 "Sun Valley" look)."""

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
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .sorter import (
    UNSORTED,
    categories_from_config,
    ensure_config_file,
    find_images,
    load_config,
    sort_photos,
    undo_sort_log,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    import sv_ttk
except ImportError:
    sv_ttk = None

try:
    import darkdetect
except ImportError:
    darkdetect = None

POLL_MS = 80
MAX_EVENTS_PER_POLL = 500

MODE_HELP = {
    "copy": "Originals stay where they are. Sorted copies go into category folders.",
    "move": "Photos are moved into category folders. Nothing is ever deleted or "
            "overwritten, and a sort log is saved so you can undo it.",
}


def _font(size: int, weight: str = "normal") -> tuple:
    family = "Segoe UI Variable Display" if os.name == "nt" else "Segoe UI"
    return (family, size, weight)


class DroneSorterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Drone Pitch Sorter")
        root.geometry("860x820")
        root.minsize(720, 640)

        self.source_dirs: list[Path] = []
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.start_time = 0.0
        self.photo_count: int | None = None
        self._count_token = 0
        self._count_shows_status = True

        self.mode = tk.StringVar(value="copy")
        self.preview = tk.BooleanVar(value=False)
        self.tolerance = tk.StringVar()

        try:
            self.config_file = ensure_config_file()
        except OSError:
            self.config_file = None
        self.tolerance.set(str(load_config(self.config_file).get("tolerance", 0.5)))

        self._apply_theme(darkdetect.theme().lower() if darkdetect and darkdetect.theme() else "light")
        self._build()
        self.mode.trace_add("write", lambda *_: self._refresh_mode())
        self.preview.trace_add("write", lambda *_: self._refresh_start_button())
        self._refresh_mode()
        self._refresh_start_button()
        self._log("Ready. Gimbal pitch is read straight from each photo.")
        self.root.after(POLL_MS, self._poll_events)

    # ------------------------------------------------------------- theme ---
    def _apply_theme(self, name: str):
        self.theme = name
        if sv_ttk:
            sv_ttk.set_theme(name)
        style = ttk.Style()
        self.c_bg = style.lookup("TFrame", "background") or "#fafafa"
        self.c_fg = style.lookup("TLabel", "foreground") or "#1c1c1c"
        dark = name == "dark"
        self.c_muted = "#9a9a9a" if dark else "#6b6b6b"
        self.c_accent = "#57c8ff" if dark else "#005fb8"
        self.c_warn = "#fce100" if dark else "#9d5d00"
        self.c_err = "#ff99a4" if dark else "#c42b1c"
        self.c_ok = "#6ccb5f" if dark else "#0f7b0f"
        self.c_field = "#2b2b2b" if dark else "#ffffff"
        style.configure("Title.TLabel", font=_font(22, "bold"))
        style.configure("Sub.TLabel", font=_font(10), foreground=self.c_muted)
        style.configure("Step.TLabel", font=_font(12, "bold"))
        style.configure("Muted.TLabel", font=_font(9), foreground=self.c_muted)
        style.configure("Drop.TLabel", font=_font(11), foreground=self.c_muted)
        style.configure("Status.TLabel", font=_font(9), foreground=self.c_muted)
        style.configure("Treeview", rowheight=30)
        if hasattr(self, "log_text"):
            self._style_text()

    def _style_text(self):
        self.log_text.configure(bg=self.c_field, fg=self.c_fg, insertbackground=self.c_fg,
                                selectbackground=self.c_accent)
        for tag, color in (("ok", self.c_ok), ("warn", self.c_warn),
                           ("err", self.c_err), ("muted", self.c_muted)):
            self.log_text.tag_config(tag, foreground=color)
        self.results.tag_configure("unsorted", foreground=self.c_warn)
        self.drop_icon.configure(foreground=self.c_accent)

    def _toggle_theme(self):
        self._apply_theme("light" if self.theme == "dark" else "dark")

    # ------------------------------------------------------------ layout ---
    def _build(self):
        outer = ttk.Frame(self.root, padding=(28, 22, 28, 20))
        outer.pack(fill=tk.BOTH, expand=True)

        # Header
        head = ttk.Frame(outer)
        head.pack(fill=tk.X)
        titles = ttk.Frame(head)
        titles.pack(side=tk.LEFT)
        ttk.Label(titles, text="Drone Pitch Sorter", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Sorts DJI photos into folders by gimbal angle",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(head, text="◐", width=3, command=self._toggle_theme).pack(side=tk.RIGHT, anchor="n")
        ttk.Label(head, text=f"v{__version__}", style="Muted.TLabel").pack(side=tk.RIGHT, anchor="n",
                                                                          padx=(0, 10), pady=(8, 0))

        # Step 1: folders
        self._step(outer, "1", "Choose flight folders")
        card = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card.pack(fill=tk.X)
        drop = ttk.Frame(card)
        drop.pack(fill=tk.X)
        self.drop_icon = ttk.Label(drop, text="⇩", font=_font(26))
        self.drop_icon.pack(side=tk.LEFT, padx=(4, 14))
        drop_txt = ttk.Frame(drop)
        drop_txt.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(drop_txt, text="Drop flight folders anywhere on this window" if HAS_DND
                  else "Add the folders you want to sort", font=_font(11, "bold")).pack(anchor="w")
        self.lbl_folders = ttk.Label(drop_txt, text="No folders added yet", style="Muted.TLabel")
        self.lbl_folders.pack(anchor="w", pady=(2, 0))
        self.btn_clear = ttk.Button(drop, text="Clear", command=self._clear_sources)
        self.btn_clear.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_browse = ttk.Button(drop, text="Add folder…", command=self._browse)
        self.btn_browse.pack(side=tk.RIGHT)

        # Step 2: options
        self._step(outer, "2", "Choose how to sort")
        card2 = ttk.Frame(outer, style="Card.TFrame", padding=16)
        card2.pack(fill=tk.X)
        row = ttk.Frame(card2)
        row.pack(fill=tk.X)
        seg = ttk.Frame(row)
        seg.pack(side=tk.LEFT)
        self.rb_copy = ttk.Radiobutton(seg, text="Copy", value="copy", variable=self.mode,
                                       style="Toggle.TButton", width=8)
        self.rb_copy.pack(side=tk.LEFT)
        self.rb_move = ttk.Radiobutton(seg, text="Move", value="move", variable=self.mode,
                                       style="Toggle.TButton", width=8)
        self.rb_move.pack(side=tk.LEFT, padx=(4, 0))
        self.sw_preview = ttk.Checkbutton(row, text="Preview only", variable=self.preview,
                                          style="Switch.TCheckbutton")
        self.sw_preview.pack(side=tk.LEFT, padx=(24, 0))
        self.btn_config = ttk.Button(row, text="Edit angles…", command=self._open_config)
        self.btn_config.pack(side=tk.RIGHT)
        tol = ttk.Frame(row)
        tol.pack(side=tk.RIGHT, padx=(0, 16))
        ttk.Label(tol, text="Tolerance ±").pack(side=tk.LEFT)
        self.spin_tol = ttk.Spinbox(tol, from_=0.1, to=10, increment=0.1, width=5,
                                    textvariable=self.tolerance)
        self.spin_tol.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Label(tol, text="°").pack(side=tk.LEFT)
        self.lbl_mode = ttk.Label(card2, style="Muted.TLabel", wraplength=740, justify="left")
        self.lbl_mode.pack(anchor="w", pady=(12, 0))

        # Step 3: run
        self._step(outer, "3", "Sort")
        run = ttk.Frame(outer)
        run.pack(fill=tk.X)
        self.btn_start = ttk.Button(run, text="Sort photos", style="Accent.TButton",
                                    command=self._start, width=22)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_cancel = ttk.Button(run, text="Cancel", command=self._cancel)
        self.btn_cancel.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_undo = ttk.Button(run, text="Undo a move…", command=self._undo)
        self.btn_undo.pack(side=tk.RIGHT)
        prog = ttk.Frame(outer)
        prog.pack(fill=tk.X, pady=(14, 0))
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X)
        self.lbl_status = ttk.Label(prog, text=" ", style="Status.TLabel")
        self.lbl_status.pack(anchor="w", pady=(6, 0))

        # Results / activity tabs
        tabs = ttk.Notebook(outer)
        tabs.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        res_tab = ttk.Frame(tabs, padding=(0, 8, 0, 0))
        tabs.add(res_tab, text="  Results  ")
        self.results = ttk.Treeview(res_tab, columns=("photos", "share"), show="tree headings",
                                    selectmode="none", height=8)
        self.results.heading("#0", text="   Folder", anchor="w")
        self.results.heading("photos", text="Photos", anchor="e")
        self.results.heading("share", text="Share", anchor="e")
        self.results.column("#0", stretch=True, anchor="w")
        self.results.column("photos", width=110, stretch=False, anchor="e")
        self.results.column("share", width=110, stretch=False, anchor="e")
        self.results.pack(fill=tk.BOTH, expand=True)

        log_tab = ttk.Frame(tabs, padding=(0, 8, 0, 0))
        tabs.add(log_tab, text="  Activity  ")
        self.log_text = tk.Text(log_tab, height=8, wrap="none", bd=0, highlightthickness=0,
                                font=("Cascadia Mono", 9) if os.name == "nt" else ("DejaVu Sans Mono", 9),
                                padx=10, pady=8, state=tk.DISABLED)
        sb = ttk.Scrollbar(log_tab, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tabs = tabs

        self._style_text()
        self._set_running(False)

        if HAS_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._handle_drop)

    def _step(self, parent, num: str, text: str):
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, pady=(20, 8))
        ttk.Label(f, text=f"{num}   {text}", style="Step.TLabel").pack(side=tk.LEFT)

    # --------------------------------------------------------------- log ---
    def _log(self, *lines):
        """Append lines; each line is text or (text, tag)."""
        at_bottom = self.log_text.yview()[1] >= 0.98
        self.log_text.config(state=tk.NORMAL)
        for line in lines:
            text, tag = (line, None) if isinstance(line, str) else line
            self.log_text.insert(tk.END, text + "\n", tag or ())
        if at_bottom:
            self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ----------------------------------------------------------- folders ---
    def _handle_drop(self, event):
        if self._busy():
            return
        paths = [Path(p) for p in self.root.tk.splitlist(event.data)]
        dirs = [p for p in paths if p.is_dir()]
        if dirs:
            self._add_sources(dirs)
        else:
            self._log(("Only folders can be dropped here.", "err"))

    def _browse(self):
        folder = filedialog.askdirectory(title="Choose a flight folder")
        if folder:
            self._add_sources([Path(folder)])

    def _add_sources(self, dirs: list[Path]):
        for d in dirs:
            if d not in self.source_dirs:
                self.source_dirs.append(d)
        self._sources_changed()

    def _clear_sources(self):
        self.source_dirs = []
        self._sources_changed()

    def _sources_changed(self):
        n = len(self.source_dirs)
        if n == 0:
            self.lbl_folders.config(text="No folders added yet")
        elif n == 1:
            self.lbl_folders.config(text=str(self.source_dirs[0]))
        else:
            names = ", ".join(d.name for d in self.source_dirs[:4])
            more = f" and {n - 4} more" if n > 4 else ""
            self.lbl_folders.config(text=f"{n} folders: {names}{more}")
        self.progress["value"] = 0
        self.lbl_status.config(text=" ")
        self.photo_count = None
        self._refresh_start_button()
        if n:
            self._count_photos()

    def _count_photos(self, show_status: bool = True):
        """Count photos in the background so the button can say how many."""
        self._count_token += 1
        self._count_shows_status = show_status
        token, dirs = self._count_token, list(self.source_dirs)
        cats = self._categories(silent=True) or []
        names = {c.name for c in cats} | {UNSORTED}
        if show_status:
            self.lbl_status.config(text="Looking for photos…")

        def work():
            try:
                n = len(find_images(dirs, names))
            except OSError:
                n = None
            self.events.put(("count", token, n))
        threading.Thread(target=work, daemon=True).start()

    def _open_config(self):
        if not self.config_file:
            messagebox.showerror("Angles", "Couldn't create categories.json next to the app.")
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
            messagebox.showerror("Angles", f"Couldn't open {path}\n\n{e}")
            return
        self._log(("Edit and save categories.json. Changes apply the next time you sort.", "muted"))

    # ----------------------------------------------------------- state ---
    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _refresh_mode(self):
        self.lbl_mode.config(text=MODE_HELP[self.mode.get()])
        self._refresh_start_button()

    def _refresh_start_button(self):
        verb = "Preview" if self.preview.get() else ("Move" if self.mode.get() == "move" else "Copy")
        if self.photo_count:
            self.btn_start.config(text=f"{verb} {self.photo_count:,} photos")
        else:
            self.btn_start.config(text=f"{verb} photos")
        if not self._busy():
            ready = bool(self.source_dirs) and self.photo_count != 0
            self.btn_start.state(["!disabled"] if ready else ["disabled"])

    def _set_running(self, running: bool):
        for w in (self.btn_start, self.btn_browse, self.btn_clear, self.btn_config, self.btn_undo,
                  self.rb_copy, self.rb_move, self.sw_preview, self.spin_tol):
            w.state(["disabled"] if running else ["!disabled"])
        self.btn_cancel.state(["!disabled"] if running else ["disabled"])
        if not running:
            self._refresh_start_button()

    def _categories(self, silent=False):
        try:
            text = self.tolerance.get().strip()
            tol = float(text) if text else None
            cats = categories_from_config(load_config(self.config_file), tol)
        except (ValueError, KeyError, TypeError) as e:
            if not silent:
                messagebox.showerror("Settings", f"The tolerance or categories.json isn't valid:\n\n{e}")
            return None
        if not cats and not silent:
            messagebox.showerror("Settings", "categories.json doesn't list any angles.")
            return None
        return cats

    # --------------------------------------------------------------- run ---
    def _start(self):
        if not self.source_dirs or self._busy():
            return
        categories = self._categories()
        if not categories:
            return
        move, dry = self.mode.get() == "move", self.preview.get()
        if move and not dry and not messagebox.askyesno(
            "Move photos?",
            "Photos will be moved into category folders inside each flight folder.\n\n"
            "Nothing is deleted or overwritten, and a sort log is saved so you can "
            "undo this with \"Undo a move…\".\n\nContinue?",
        ):
            return

        self.cancel_event.clear()
        self._set_running(True)
        self.progress["value"] = 0
        self.results.delete(*self.results.get_children())
        self.start_time = time.monotonic()
        self.lbl_status.config(text="Starting…")
        verb = "Previewing" if dry else ("Moving" if move else "Copying")
        self._log("", (f"— {verb} photos —", "muted"))

        def work():
            try:
                results = sort_photos(
                    self.source_dirs, categories, move=move, dry_run=dry,
                    on_progress=lambda i, n, r: self.events.put(("progress", i, n, r)),
                    cancel=self.cancel_event,
                )
                self.events.put(("done", results, dry, move))
            except Exception as e:
                self.events.put(("fatal", e))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _cancel(self):
        self.cancel_event.set()
        self.btn_cancel.state(["disabled"])
        self.lbl_status.config(text="Stopping after the current photo…")

    def _undo(self):
        initial = str(self.source_dirs[0]) if self.source_dirs else None
        path = filedialog.askopenfilename(
            title="Choose the sort log to undo", initialdir=initial,
            filetypes=[("Sort logs", "Sort log *.csv"), ("CSV files", "*.csv")],
        )
        if not path or not messagebox.askyesno(
            "Undo move?", "Moved photos in this log go back to where they came from.\n"
                          "Nothing is overwritten. Continue?"):
            return
        self._set_running(True)
        self.btn_cancel.state(["disabled"])
        self.lbl_status.config(text="Putting photos back…")

        def work():
            try:
                self.events.put(("undone", undo_sort_log(Path(path))))
            except Exception as e:
                self.events.put(("fatal", e))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------ events ---
    def _poll_events(self):
        lines, last = [], None
        try:
            for _ in range(MAX_EVENTS_PER_POLL):
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    last = ev[1:3]
                    lines.append(self._format_result(ev[3]))
                    continue
                if lines:
                    self._log(*lines)
                    lines = []
                if last:  # apply progress before a "done" event, not after it
                    self._update_progress(*last)
                    last = None
                self._handle_event(ev)
        except queue.Empty:
            pass
        if lines:
            self._log(*lines)
        if last:
            self._update_progress(*last)
        self.root.after(POLL_MS, self._poll_events)

    def _handle_event(self, ev):
        kind = ev[0]
        if kind == "count":
            _, token, n = ev
            if token == self._count_token:
                self.photo_count = n
                if self._count_shows_status:
                    self.lbl_status.config(
                        text="No photos found in these folders (already sorted?)" if n == 0
                        else f"{n:,} photos ready to sort" if n else " ")
                self._refresh_start_button()
        elif kind == "done":
            self._finish(*ev[1:])
        elif kind == "undone":
            self._set_running(False)
            counts = Counter(s for s, _, _ in ev[1])
            for status, path, detail in ev[1]:
                if status != "restored":
                    self._log((f"{status:9} {path.name}: {detail}", "warn"))
            self._log((f"Undo finished: {counts.get('restored', 0):,} photos put back.", "ok"))
            self.lbl_status.config(text=f"Undo finished: {counts.get('restored', 0):,} photos put back")
            if self.source_dirs:
                self._count_photos(show_status=False)
        elif kind == "fatal":
            self._set_running(False)
            self._log((f"Something went wrong: {ev[1]}", "err"))
            self.lbl_status.config(text=f"Something went wrong: {ev[1]}")

    @staticmethod
    def _format_result(r):
        pitch = f"{r.pitch:6.1f}°" if r.pitch is not None else "   N/A "
        name = f"{r.source.parent.name}/{r.source.name}"
        if r.action == "error":
            return (f"error     {name}: {r.error}", "err")
        renamed = f"  (saved as {r.dest.name})" if r.dest and r.dest.name != r.source.name else ""
        tag = "warn" if r.category == UNSORTED or r.action == "skipped" else None
        return (f"{r.action:9} {pitch}  {name}  →  {r.category}{renamed}", tag)

    def _update_progress(self, i: int, n: int):
        self.progress["value"] = (i / n) * 100 if n else 100
        if self.cancel_event.is_set():
            return
        elapsed = time.monotonic() - self.start_time
        text = f"{i:,} of {n:,}"
        if elapsed > 0.5 and i:
            rate = i / elapsed
            left = int((n - i) / rate)
            mins, secs = divmod(left, 60)
            text += f"  ·  {rate:,.0f} photos/s  ·  about {f'{mins}m {secs}s' if mins else f'{secs}s'} left"
        self.lbl_status.config(text=text)

    def _finish(self, results, dry, move):
        self._set_running(False)
        cancelled = self.cancel_event.is_set()
        if not results:
            self.lbl_status.config(text="No photos to sort.")
            return
        elapsed = time.monotonic() - self.start_time
        by_cat = Counter(r.category for r in results)
        by_action = Counter(r.action for r in results)
        total = len(results)

        for cat, count in sorted(by_cat.items(), key=lambda kv: (kv[0] == UNSORTED, -kv[1])):
            self.results.insert("", tk.END, text=f"  {cat}", values=(f"{count:,}", f"{count / total:.0%}"),
                                tags=("unsorted",) if cat == UNSORTED else ())

        done_word = {"copied": "copied", "moved": "moved", "planned": "would be sorted",
                     "skipped": "already sorted", "error": "failed"}
        parts = [f"{c:,} {done_word.get(a, a)}" for a, c in by_action.items()]
        head = "Preview" if dry else ("Stopped" if cancelled else "Done")
        summary = f"{head}: {', '.join(parts)} in {elapsed:.1f}s"
        if move and not dry and by_action.get("moved"):
            summary += "  ·  sort log saved in each folder"
        self.lbl_status.config(text=summary)
        self._log((summary, "err" if by_action.get("error") else "ok"))
        if not cancelled:
            self.progress["value"] = 100
        if not dry:
            self._count_photos(show_status=False)


def _enable_crisp_text_on_windows():
    """Without this, Windows scales the window up as a blurry bitmap on high-DPI screens."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main():
    _enable_crisp_text_on_windows()
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    DroneSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
