"""Environments table widget displaying discovered environments and handling sorting and selection."""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from venvdiscover.model import Env, Kind, LOCKED_KINDS
from venvdiscover.query import env_sort_key
from venvdiscover.ui.format import fmt_date, fmt_size
from venvdiscover.ui.theme import get_kind_image, px

COLUMNS = ("name", "kind", "python", "packages", "size", "last_used", "location")

HEADER_TITLES = {
    "name": "Name",
    "kind": "Kind",
    "python": "Python",
    "packages": "Packages",
    "size": "Size",
    "last_used": "Last used",
    "location": "Location",
}

COLUMN_ANCHORS = {
    "name": "w",
    "kind": "w",
    "python": "center",
    "packages": "e",
    "size": "e",
    "last_used": "w",
    "location": "w",
}


class EnvsTable(ttk.Frame):
    """Table showing discovered environments with sortable columns and multi-selection."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        on_select: Callable[[list[str]], None] | None = None,
        on_double_click: Callable[[str], None] | None = None,
        on_context_menu: Callable[[tk.Event], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.on_select = on_select
        self.on_double_click = on_double_click
        self.on_context_menu = on_context_menu

        self._envs: dict[str, Env] = {}
        self._sort_col: str | None = None
        self._sort_rev: bool = False

        # Determine column widths measured from the font
        try:
            f_def = tkfont.nametofont("VDDefault")
        except Exception:
            f_def = tkfont.nametofont("TkDefaultFont")

        try:
            f_head = tkfont.nametofont("VDHeading")
        except Exception:
            f_head = f_def

        # Treeview with tree column for kind markers and columns for attributes
        self.tree = ttk.Treeview(
            self,
            columns=COLUMNS,
            show="tree headings",
            selectmode="extended",
        )
        # Kind dot (#0) gets room for the dot plus a comfortable gap on each side
        dot_w = px(self.tree, 28)
        self.tree.column("#0", width=dot_w, minwidth=px(self.tree, 24), stretch=False, anchor="center")
        self.tree.heading("#0", text="")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Configure columns measured from the font on widest real values plus padding:
        # Kind: "python install (locked)"
        # Size: "999.9 MB"
        # Last used: "2026-10-27"
        # Python: "3.10.18"
        # Packages: heading "Packages"
        # Name: sensible cap (~140 px)
        w_kind = max(f_head.measure(HEADER_TITLES["kind"]), f_def.measure("python install (locked)")) + px(self.tree, 18)
        w_size = max(f_head.measure(HEADER_TITLES["size"]), f_def.measure("999.9 MB")) + px(self.tree, 16)
        w_last = max(f_head.measure(HEADER_TITLES["last_used"]), f_def.measure("2026-10-27")) + px(self.tree, 18)
        w_py = max(f_head.measure(HEADER_TITLES["python"]), f_def.measure("3.10.18")) + px(self.tree, 16)
        w_pkg = max(f_head.measure(HEADER_TITLES["packages"]), f_def.measure("999")) + px(self.tree, 16)
        w_name = px(self.tree, 140)

        self.tree.column("name", width=w_name, minwidth=px(self.tree, 70), stretch=False, anchor="w")
        self.tree.column("kind", width=w_kind, minwidth=px(self.tree, 90), stretch=False, anchor="w")
        self.tree.column("python", width=w_py, minwidth=px(self.tree, 40), stretch=False, anchor="center")
        self.tree.column("packages", width=w_pkg, minwidth=px(self.tree, 45), stretch=False, anchor="e")
        self.tree.column("size", width=w_size, minwidth=px(self.tree, 40), stretch=False, anchor="e")
        self.tree.column("last_used", width=w_last, minwidth=px(self.tree, 60), stretch=False, anchor="w")
        self.tree.column("location", width=px(self.tree, 200), minwidth=px(self.tree, 120), stretch=True, anchor="w")

        for col in COLUMNS:
            self.tree.heading(
                col,
                text=HEADER_TITLES[col],
                anchor=COLUMN_ANCHORS[col],
                command=lambda c=col: self._on_heading_click(c),
            )

        # Tags for striping and temporary venvs
        self.tree.tag_configure("temp", foreground="#888888")

        # Event bindings
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_context_menu)

    def _format_values(self, env: Env) -> tuple[str, ...]:
        if env.kind in LOCKED_KINDS:
            kind_str = f"{env.kind.value} (locked)"
        else:
            kind_str = env.kind.value

        py_str = env.python_version or ""
        pkg_str = str(len(env.packages)) if (env.packages or env.stamp > 0) else ""
        size_str = fmt_size(env.size)
        lu_str = f"  {fmt_date(env.last_used)}"
        loc_str = env.path

        return (env.name, kind_str, py_str, pkg_str, size_str, lu_str, loc_str)

    def _refresh_stripes(self) -> None:
        """Update alternating even/odd tags preserving temp tag."""
        for idx, iid in enumerate(self.tree.get_children()):
            env = self._envs.get(iid)
            stripe = "even" if idx % 2 == 0 else "odd"
            if env and env.kind == Kind.TEMP_VENV:
                tags = (stripe, "temp")
            else:
                tags = (stripe,)
            self.tree.item(iid, tags=tags)

    def upsert(self, env: Env) -> None:
        """Insert or update an environment row."""
        self._envs[env.key] = env
        vals = self._format_values(env)
        img = get_kind_image(env.kind, master=self.tree)

        if self.tree.exists(env.key):
            self.tree.item(env.key, values=vals, image=img)
            if self._sort_col:
                self.sort_by(self._sort_col, self._sort_rev)
            else:
                self._refresh_stripes()
        else:
            if self._sort_col:
                self.tree.insert("", "end", iid=env.key, values=vals, image=img)
                self.sort_by(self._sort_col, self._sort_rev)
            else:
                if env.kind == Kind.TEMP_VENV:
                    self.tree.insert("", "end", iid=env.key, values=vals, image=img)
                else:
                    children = self.tree.get_children()
                    insert_idx: int | str = "end"
                    for idx, child_key in enumerate(children):
                        child_env = self._envs.get(child_key)
                        if child_env and child_env.kind == Kind.TEMP_VENV:
                            insert_idx = idx
                            break
                    self.tree.insert("", insert_idx, iid=env.key, values=vals, image=img)
                self._refresh_stripes()

    def remove(self, key: str) -> None:
        """Remove an environment row."""
        self._envs.pop(key, None)
        if self.tree.exists(key):
            self.tree.delete(key)
            self._refresh_stripes()

    def selected_keys(self) -> list[str]:
        """Return list of selected environment keys."""
        return list(self.tree.selection())

    def sort_by(self, column: str, reverse: bool = False) -> None:
        """Sort environments by column. Temp venvs always remain at the end in both directions."""
        self._sort_col = column
        self._sort_rev = reverse

        non_temp = [e for e in self._envs.values() if e.kind != Kind.TEMP_VENV]
        temp = [e for e in self._envs.values() if e.kind == Kind.TEMP_VENV]

        non_temp.sort(key=lambda e: env_sort_key(e, column), reverse=reverse)
        temp.sort(key=lambda e: env_sort_key(e, column), reverse=reverse)
        sorted_envs = non_temp + temp

        for idx, env in enumerate(sorted_envs):
            if self.tree.exists(env.key):
                self.tree.move(env.key, "", idx)
        self._refresh_stripes()

    def _on_heading_click(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self.sort_by(col, reverse=self._sort_rev)

    def _on_tree_select(self, event: tk.Event) -> None:
        if self.on_select:
            self.on_select(self.selected_keys())

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and self.on_double_click:
            self.on_double_click(item)

    def _on_tree_context_menu(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
            if self.on_select:
                self.on_select(self.selected_keys())
        if self.on_context_menu:
            self.on_context_menu(event)
