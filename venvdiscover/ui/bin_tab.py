"""Bin tab widget showing binned environments with restore and purge actions."""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
from tkinter import ttk

from venvdiscover.trashbin import BinItem
from venvdiscover.ui.format import fmt_date, fmt_size
from venvdiscover.ui.theme import px


class BinTab(ttk.Frame):
    """Tab displaying binned environments with actions to restore or delete forever."""

    COLUMNS = ("name", "original location", "size", "deleted", "days left")

    def __init__(
        self,
        master: tk.Misc | None,
        on_restore: Callable[[BinItem], None],
        on_forget: Callable[[BinItem], None],
    ):
        super().__init__(master)
        self.on_restore = on_restore
        self.on_forget = on_forget
        self._items: dict[str, BinItem] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=self.COLUMNS,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("name", text="Name")
        self.tree.heading("original location", text="Original Location")
        self.tree.heading("size", text="Size")
        self.tree.heading("deleted", text="Deleted")
        self.tree.heading("days left", text="Days Left")

        self.tree.column("name", width=px(self.tree, 140), minwidth=px(self.tree, 80))
        self.tree.column("original location", width=px(self.tree, 340), minwidth=px(self.tree, 150))
        self.tree.column("size", width=px(self.tree, 80), minwidth=px(self.tree, 60), anchor="e")
        self.tree.column("deleted", width=px(self.tree, 100), minwidth=px(self.tree, 80), anchor="center")
        self.tree.column("days left", width=px(self.tree, 80), minwidth=px(self.tree, 60), anchor="e")

        vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        btn_frame = ttk.Frame(self, padding=5)
        btn_frame.pack(fill="x")

        self.restore_btn = ttk.Button(
            btn_frame,
            text="Restore",
            command=self._handle_restore,
            state="disabled",
        )
        self.forget_btn = ttk.Button(
            btn_frame,
            text="Delete forever",
            command=self._handle_forget,
            state="disabled",
        )

        self.restore_btn.pack(side="left", padx=5)
        self.forget_btn.pack(side="left", padx=5)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _on_select(self, event=None) -> None:
        has_sel = bool(self.tree.selection())
        state = "normal" if has_sel else "disabled"
        self.restore_btn.configure(state=state)
        self.forget_btn.configure(state=state)

    def _handle_restore(self) -> None:
        item = self.selected_item()
        if item and self.on_restore:
            self.on_restore(item)

    def _handle_forget(self) -> None:
        item = self.selected_item()
        if item and self.on_forget:
            self.on_forget(item)

    def selected_item(self) -> BinItem | None:
        sel = self.tree.selection()
        if sel and sel[0] in self._items:
            return self._items[sel[0]]
        return None

    def set_items(self, items: list[BinItem], now: float) -> None:
        """Populate the treeview rows with provided bin items and calculate days left."""
        prev_sel = self.tree.selection()
        for child in self.tree.get_children():
            self.tree.delete(child)
        self._items.clear()

        for item in items:
            days = item.days_left(now)
            vals = (
                item.name,
                item.original_path,
                fmt_size(item.size),
                fmt_date(item.deleted_at),
                str(days),
            )
            self.tree.insert("", "end", iid=item.id, values=vals)
            self._items[item.id] = item

        if prev_sel and prev_sel[0] in self._items:
            self.tree.selection_set(prev_sel[0])

        self._on_select()
