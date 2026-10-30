"""Package Manager Caches dialog: find and safely clean uv, poetry, pipenv and pip caches."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable

from venvdiscover.caches import Cache, clean_cache, find_caches
from venvdiscover.ui.format import fmt_date, fmt_frees, fmt_size, shares_notably
from venvdiscover.ui.theme import apply_theme, px, style_window

if TYPE_CHECKING:
    from venvdiscover.ui.app import App


class CacheCleanDialog(tk.Toplevel):
    """Dialog displaying streamed output from cleaning a package cache."""

    def __init__(self, parent: tk.Misc, title: str = "Clean Cache") -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry(f"{px(self, 600)}x{px(self, 350)}")
        if parent and getattr(parent, "winfo_viewable", lambda: False)():
            try:
                self.transient(parent)
            except Exception:
                pass

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(frame, wrap="word", width=70, height=15)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set, state="disabled")

        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_frame.pack(fill="x")

        self.close_btn = ttk.Button(btn_frame, text="Close", command=self.destroy, state="disabled")
        self.close_btn.pack(side="right")
        style_window(self)

    def append_line(self, line: str) -> None:
        if not self.winfo_exists():
            return
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def finish(self) -> None:
        if not self.winfo_exists():
            return
        self.close_btn.configure(state="normal")
        self.close_btn.focus_set()


class CachesDialog(tk.Toplevel):
    """Dialog listing discovered package manager caches with safe clean actions."""

    def __init__(self, parent: App) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("Package Manager Caches")
        self.geometry(f"{px(self, 780)}x{px(self, 460)}")
        if parent and getattr(parent, "winfo_viewable", lambda: False)():
            try:
                self.transient(parent)
            except Exception:
                pass

        self._caches: list[Cache] = []
        self._load_thread: threading.Thread | None = None

        pad_12 = px(self, 12)
        frame = ttk.Frame(self, padding=pad_12)
        frame.pack(fill="both", expand=True)

        header_frame = ttk.Frame(frame)
        header_frame.pack(fill="x", pady=(0, px(self, 8)))

        self.lbl_title = ttk.Label(header_frame, text="PACKAGE MANAGER CACHES", font="VDHeading")
        self.lbl_title.pack(anchor="w")

        self.lbl_sub = ttk.Label(
            header_frame,
            text="Find and safely clean uv, poetry, pipenv and pip package caches.",
        )
        self.lbl_sub.pack(anchor="w", pady=(px(self, 2), 0))

        # Treeview table
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True, pady=(px(self, 4), px(self, 8)))

        self.columns = ("tool", "location", "size", "last_modified", "clean_method")
        self.tree = ttk.Treeview(
            table_frame,
            columns=self.columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("tool", text="Tool")
        self.tree.heading("location", text="Location")
        self.tree.heading("size", text="Size")
        self.tree.heading("last_modified", text="Last modified")
        self.tree.heading("clean_method", text="Clean method")

        self.tree.column("tool", width=px(self, 70), anchor="w")
        self.tree.column("location", width=px(self, 330), anchor="w")
        self.tree.column("size", width=px(self, 80), anchor="e")
        self.tree.column("last_modified", width=px(self, 100), anchor="w")
        self.tree.column("clean_method", width=px(self, 140), anchor="w")

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", lambda e: self._on_clean())

        # Action buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x")

        self.btn_clean = ttk.Button(btn_frame, text="Clean…", command=self._on_clean, state="disabled")
        self.btn_clean.pack(side="left", padx=(0, px(self, 6)))

        self.btn_refresh = ttk.Button(btn_frame, text="Refresh", command=self.load_caches)
        self.btn_refresh.pack(side="left")

        self.btn_close = ttk.Button(btn_frame, text="Close", command=self.destroy)
        self.btn_close.pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        theme = getattr(parent, "current_theme", "dark")
        palette = getattr(parent, "current_palette", "default")
        apply_theme(self, theme, palette)
        style_window(self)

        self.load_caches()

    def _post_ui(self, fn: Callable[[], None]) -> None:
        """Safely marshal callback to Tk main event loop."""
        events = getattr(self.parent_app, "events", None)
        if events is not None:
            try:
                from venvdiscover.ui.app import UIAction
                events.put(UIAction(fn))
                return
            except Exception:
                pass
        try:
            self.after(0, fn)
        except Exception:
            pass

    def load_caches(self) -> None:
        """Measure caches in a background worker thread."""
        if not self.winfo_exists():
            return
        self.btn_refresh.configure(state="disabled")

        def worker() -> None:
            caches = find_caches(self.parent_app.places)
            self._post_ui(lambda: self._apply_loaded_caches(caches))

        self._load_thread = threading.Thread(target=worker, daemon=True)
        self._load_thread.start()

    def _apply_loaded_caches(self, caches: list[Cache]) -> None:
        if not self.winfo_exists():
            return
        self._caches = caches
        self.tree.delete(*self.tree.get_children())
        for idx, c in enumerate(caches):
            sz_str = fmt_frees(c.size, c.size_unique)
            dt_str = fmt_date(c.last_modified) if c.last_modified > 0 else "—"
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(c.tool, c.path, sz_str, dt_str, c.clean_method),
            )
        self.btn_refresh.configure(state="normal")
        self._on_tree_select(None)

    def _selected_cache(self) -> Cache | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
            if 0 <= idx < len(self._caches):
                return self._caches[idx]
        except (ValueError, IndexError):
            pass
        return None

    def _on_tree_select(self, event: Any) -> None:
        cache = self._selected_cache()
        if cache is not None:
            self.btn_clean.configure(state="normal")
        else:
            self.btn_clean.configure(state="disabled")

    def _on_clean(self) -> None:
        cache = self._selected_cache()
        if cache is None:
            return

        if shares_notably(cache.size, cache.size_unique):
            assert cache.size_unique is not None
            size_text = (f"frees {fmt_size(cache.size_unique)}; {fmt_size(cache.size - cache.size_unique)} of its "
                         f"{fmt_size(cache.size)} is also linked into environments and stays")
        else:
            size_text = fmt_size(cache.size)
        if cache.tool == "pipenv":
            msg = (
                f"Clean {cache.tool} cache ({size_text}) at:\n"
                f"{cache.path}\n\n"
                "Note: pipenv --clear clears both pipenv's and pip's package caches.\n"
                "Cached packages will be re-downloaded when needed."
            )
        else:
            msg = (
                f"Clean {cache.tool} cache ({size_text}) at:\n"
                f"{cache.path}\n\n"
                "Cached packages will be re-downloaded when needed."
            )
        confirmed = messagebox.askyesno(
            f"Clean {cache.tool.capitalize()} Cache",
            msg,
            parent=self,
        )
        if not confirmed:
            return

        dlg = CacheCleanDialog(self, title=f"Clean {cache.tool.capitalize()} Cache")
        try:
            dlg.grab_set()
        except Exception:
            pass

        def worker() -> None:
            def on_line(line: str) -> None:
                if dlg.winfo_exists():
                    self._post_ui(lambda l=line: dlg.append_line(l))

            try:
                if hasattr(self.parent_app, "_log_action"):
                    self.parent_app._log_action("clean_cache", cache.path, "started")
                exit_code = clean_cache(cache, on_line=on_line)
                if hasattr(self.parent_app, "_log_action"):
                    self.parent_app._log_action("clean_cache", cache.path, f"exit code {exit_code}")
                if dlg.winfo_exists():
                    self._post_ui(lambda c=exit_code: dlg.append_line(f"\nFinished (exit code {c})"))
            except Exception as exc:
                if dlg.winfo_exists():
                    self._post_ui(lambda e=exc: dlg.append_line(f"\nError: {e}"))
            finally:
                if dlg.winfo_exists():
                    self._post_ui(dlg.finish)
                if self.winfo_exists():
                    self._post_ui(self.load_caches)

        threading.Thread(target=worker, daemon=True).start()
