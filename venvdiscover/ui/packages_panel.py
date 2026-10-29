"""Packages panel and search results widgets."""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
from tkinter import ttk

from venvdiscover.model import Env, Package
from venvdiscover.query import filter_packages
from venvdiscover.ui.theme import px


class PackagesPanel(ttk.Frame):
    """Panel displaying packages for the selected environment with a filter box."""

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        super().__init__(master, style="CardInner.TFrame", **kwargs)
        self._current_env: Env | None = None

        # Single rounded filter entry with placeholder
        self.filter_var = tk.StringVar(value="Filter packages…")
        self.filter_entry = ttk.Entry(self, textvariable=self.filter_var)
        self.filter_entry.pack(fill="x", pady=(0, px(self, 6)))

        def _on_filter_focus_in(e):
            if self.filter_var.get() == "Filter packages…":
                self.filter_var.set("")

        def _on_filter_focus_out(e):
            if not self.filter_var.get().strip():
                self.filter_var.set("Filter packages…")

        self.filter_entry.bind("<FocusIn>", _on_filter_focus_in)
        self.filter_entry.bind("<FocusOut>", _on_filter_focus_out)
        self.filter_var.trace_add("write", self._on_filter_changed)

        # Treeview for packages
        tree_frame = ttk.Frame(self, style="CardInner.TFrame")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name", "version", "source"),
            show="headings",
            selectmode="browse",
        )
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.column("name", width=px(self.tree, 180), minwidth=px(self.tree, 100), anchor="w")
        self.tree.column("version", width=px(self.tree, 90), minwidth=px(self.tree, 60), anchor="center")
        self.tree.column("source", width=px(self.tree, 80), minwidth=px(self.tree, 50), anchor="center")

        self.tree.heading("name", text="Package")
        self.tree.heading("version", text="Version")
        self.tree.heading("source", text="Source")

        self._populate()

    def show(self, env: Env | None) -> None:
        """Display packages for the given environment, or clear if None."""
        self._current_env = env
        self._populate()

    def _on_filter_changed(self, *args) -> None:
        self._populate()

    def _populate(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        if self._current_env is None:
            self.filter_entry.config(state="disabled")
            return

        self.filter_entry.config(state="normal")
        q = self.filter_var.get().strip()
        if q == "Filter packages…":
            q = ""
        filtered = filter_packages(self._current_env.packages, q)
        for pkg in filtered:
            self.tree.insert("", "end", values=(pkg.name, pkg.version, pkg.source))


class SearchResults(ttk.Frame):
    """Panel displaying global search results across all environments."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        on_select_env: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, style="CardInner.TFrame", **kwargs)
        self.on_select_env = on_select_env

        # Header info
        header_frame = ttk.Frame(self, style="CardInner.TFrame", padding=(0, px(self, 4)))
        header_frame.pack(fill="x", pady=(0, px(self, 4)))
        self.title_label = ttk.Label(header_frame, text="Search results across all environments:", style="Muted.TLabel")
        self.title_label.pack(side="left")

        # Treeview for search results
        tree_frame = ttk.Frame(self, style="CardInner.TFrame")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("env", "package", "version"),
            show="headings",
            selectmode="browse",
        )
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.column("env", width=px(self.tree, 140), minwidth=px(self.tree, 80), anchor="w")
        self.tree.column("package", width=px(self.tree, 160), minwidth=px(self.tree, 80), anchor="w")
        self.tree.column("version", width=px(self.tree, 90), minwidth=px(self.tree, 60), anchor="center")

        self.tree.heading("env", text="Environment")
        self.tree.heading("package", text="Package")
        self.tree.heading("version", text="Version")

        # Footer count label
        self.footer_label = ttk.Label(self, text="", padding=(4, 2))
        self.footer_label.pack(fill="x")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def show(self, rows: list[tuple[Env, Package]], total: int) -> None:
        """Display search results up to 1000 items, and update count label."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for env, pkg in rows[:1000]:
            self.tree.insert("", "end", values=(env.name, pkg.name, pkg.version), tags=(env.key,))

        if total > 1000:
            self.footer_label.config(text=f"Showing first 1000 of {total}")
        elif total > 0:
            self.footer_label.config(text=f"{total} package{'s' if total != 1 else ''} found")
        else:
            self.footer_label.config(text="No packages found")

    def _on_tree_select(self, event: tk.Event) -> None:
        sel = self.tree.selection()
        if sel and self.on_select_env:
            item_tags = self.tree.item(sel[0], "tags")
            if item_tags:
                self.on_select_env(item_tags[0])
