"""Delete confirmation dialog and measure/delete background worker."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from venvdiscover.model import Env, Kind
from venvdiscover.places import Places
from venvdiscover.sizes import folder_usage
from venvdiscover.trashbin import (
    BinItem,
    DeleteError,
    delete_permanently,
    move_to_bin,
    project_marker,
)
from venvdiscover.ui.format import fmt_size, shares_notably
from venvdiscover.ui.theme import style_window


@dataclass
class Measured:
    key: str
    size: int
    project_marker: str | None = None
    size_unique: int | None = None  # what deleting frees; None = not known


@dataclass
class Deleted:
    key: str
    item: BinItem | None  # None = permanent delete


@dataclass
class DeleteFailed:
    key: str
    message: str


@dataclass
class BinChanged:
    pass


class ConfirmDeleteDialog(tk.Toplevel):
    """Dialog showing environments to be deleted, permanent/bin breakdown, and warnings."""

    def __init__(
        self,
        master: tk.Misc | None,
        to_bin: list[Env],
        permanent: list[Env],
        locked: list[Env],
        extras: dict[str, list[str]],
        on_confirm: Callable[[], None],
        cant_delete: list[tuple[Env, str]] | list[Env] | None = None,
    ):
        super().__init__(master)
        self.title("Confirm Delete")
        if master and getattr(master, "winfo_viewable", lambda: False)():
            try:
                self.transient(master)
            except Exception:
                pass
        self.resizable(True, True)
        self.on_confirm = on_confirm
        self.extras = extras or {}

        self.to_bin: list[Env] = []
        self.permanent: list[Env] = []
        self.locked: list[Env] = list(locked)
        self.cant_delete: list[tuple[Env, str]] = []

        if cant_delete:
            for item in cant_delete:
                if isinstance(item, tuple):
                    self.cant_delete.append(item)
                else:
                    m = project_marker(item.path) or "project"
                    self.cant_delete.append((item, m))

        for env in to_bin:
            marker = project_marker(env.path)
            if marker:
                self.cant_delete.append((env, marker))
            else:
                self.to_bin.append(env)

        for env in permanent:
            marker = project_marker(env.path)
            if marker:
                self.cant_delete.append((env, marker))
            else:
                self.permanent.append(env)

        self.deletable_envs: list[Env] = self.to_bin + self.permanent

        self._dialog_text = self._build_text()
        self._build_ui()
        style_window(self)

    @property
    def dialog_text(self) -> str:
        return self._dialog_text

    def get_text(self) -> str:
        return self._dialog_text

    @staticmethod
    def _size_text(env: Env) -> str:
        """'1.2 GB', or 'frees 0.9 GB (3.0 GB on disk, 2.1 GB shared with other folders)' when files are shared."""
        if shares_notably(env.size, env.size_unique):
            return (f"frees {fmt_size(env.size_unique)} ({fmt_size(env.size)} on disk, "
                    f"{fmt_size(env.shared)} shared with other folders)")
        return fmt_size(env.size)

    @staticmethod
    def _unmeasured_conda_note(env: Env) -> str | None:
        """Before the shared part is measured, warn that conda envs share files with the package cache."""
        if env.size_unique is None and (env.kind in (Kind.CONDA, Kind.CONDA_BASE) or env.conda_root):
            return "    Conda shares files with its package cache, so deleting may free less space than shown."
        return None

    def _build_text(self) -> str:
        sections: list[str] = []

        if self.to_bin:
            lines: list[str] = ["Goes to the bin (14 days):"]
            for env in self.to_bin:
                lines.append(f"  • {env.name} — {env.path} — {self._size_text(env)}")
                if self.extras.get(env.key):
                    for extra in self.extras[env.key]:
                        lines.append(f"      Will also remove: {extra}")
                if note := self._unmeasured_conda_note(env):
                    lines.append(note)
                if env.kind == Kind.APP_VENV:
                    lines.append("    This env belongs to an app. Deleting it may break that app until it rebuilds the env.")
            sections.append("\n".join(lines))

        if self.permanent:
            lines = ["Deleted permanently:"]
            for env in self.permanent:
                sz = fmt_size(env.size)
                lines.append(f"  • {env.name} — {env.path} — {self._size_text(env)}")
                lines.append(f"    This is {sz} and can only be deleted permanently.")
                if self.extras.get(env.key):
                    for extra in self.extras[env.key]:
                        lines.append(f"      Will also remove: {extra}")
                if note := self._unmeasured_conda_note(env):
                    lines.append(note)
                if env.kind == Kind.APP_VENV:
                    lines.append("    This env belongs to an app. Deleting it may break that app until it rebuilds the env.")
            sections.append("\n".join(lines))

        if self.cant_delete or self.locked:
            lines = ["Can't delete:"]
            for env, marker in self.cant_delete:
                lines.append(f"  • {env.path}")
                lines.append(f"    This folder is also a project ({marker}). Nothing was changed.")
            for env in self.locked:
                lines.append(f"  • {env.name} — {env.path}")
                lines.append(f"    Locked environment ({env.kind.value}). Cannot be deleted.")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        self.text_widget = tk.Text(main_frame, wrap="word", width=75, height=18)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.text_widget.yview)
        self.text_widget.configure(yscrollcommand=scrollbar.set)

        self.text_widget.insert("1.0", self._dialog_text)
        self.text_widget.configure(state="disabled")

        self.text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill="x")

        self.delete_btn = ttk.Button(btn_frame, text="Delete", command=self._confirm, style="Danger.TButton")
        self.cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.destroy)

        self.cancel_btn.pack(side="right", padx=(5, 0))
        self.delete_btn.pack(side="right")

        has_deletable = bool(self.to_bin or self.permanent)
        if not has_deletable:
            self.delete_btn.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.destroy())
        self.cancel_btn.focus_set()

    def _confirm(self) -> None:
        if not self.to_bin and not self.permanent:
            return
        self.destroy()
        if self.on_confirm:
            self.on_confirm()


class DeleteWorker(threading.Thread):
    """Worker thread running measure or delete operations off the UI thread."""

    def __init__(
        self,
        mode: str,
        envs: list[Env],
        places: Places,
        out: queue.Queue,
        log: Callable[[str, str, str], None],
        *,
        permanent_keys: Collection[str] = (),
    ):
        super().__init__(daemon=True)
        self.mode = mode
        self.envs = envs
        self.places = places
        self.out = out
        self.log = log
        self.permanent_keys = set(permanent_keys)

    def run(self) -> None:
        if self.mode == "measure":
            for env in self.envs:
                sz, unique = env.size, env.size_unique
                if sz is None:
                    usage = folder_usage(env.path)
                    sz, unique = usage.total, usage.unique
                marker = project_marker(env.path)
                self.out.put(Measured(key=env.key, size=sz, project_marker=marker, size_unique=unique))
        elif self.mode == "delete":
            for env in self.envs:
                try:
                    marker = project_marker(env.path)
                    if marker:
                        raise DeleteError(
                            f"This folder is also a project ({marker}). "
                            "Only the env inside it could be deleted, so nothing was changed."
                        )
                    if env.key in self.permanent_keys:
                        delete_permanently(env, self.places, self.log)
                        self.out.put(Deleted(key=env.key, item=None))
                    else:
                        item = move_to_bin(env, self.places, time.time(), self.log)
                        self.out.put(Deleted(key=env.key, item=item))
                except (DeleteError, OSError, Exception) as e:
                    self.out.put(DeleteFailed(key=env.key, message=str(e)))
            self.out.put(BinChanged())
