"""Shared Tk testing utilities for off-screen and invisible test windows."""

from __future__ import annotations

import tkinter as tk
from typing import Any

from venvdiscover.places import Places
from venvdiscover.ui.app import App, CondaCleanDialog, DrivesDialog, SettingsDialog
from venvdiscover.ui.delete_dialog import ConfirmDeleteDialog


class DummyScanner:
    """Safe no-op scanner for headless tests."""
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


OFFSCREEN_GEOMETRY = "+-32000+-32000"


def make_offscreen(w: tk.Wm) -> None:
    """Ensure window is completely transparent and positioned far off-screen."""
    try:
        w.attributes("-alpha", 0.0)
    except Exception:
        pass
    try:
        w.geometry(OFFSCREEN_GEOMETRY)
    except Exception:
        pass


_orig_tk_init = tk.Tk.__init__
_orig_toplevel_init = tk.Toplevel.__init__
_orig_toplevel_deiconify = tk.Toplevel.deiconify
_orig_tk_deiconify = tk.Tk.deiconify
_hooks_installed = False


def install_offscreen_hooks() -> None:
    """Patch Tk and Toplevel so any window opened during tests stays off-screen."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    def _tk_init(self: tk.Tk, *args: Any, **kwargs: Any) -> None:
        _orig_tk_init(self, *args, **kwargs)
        make_offscreen(self)

    def _toplevel_init(self: tk.Toplevel, *args: Any, **kwargs: Any) -> None:
        _orig_toplevel_init(self, *args, **kwargs)
        make_offscreen(self)

    def _toplevel_deiconify(self: tk.Toplevel) -> None:
        make_offscreen(self)
        _orig_toplevel_deiconify(self)
        make_offscreen(self)

    def _tk_deiconify(self: tk.Tk) -> None:
        make_offscreen(self)
        _orig_tk_deiconify(self)
        make_offscreen(self)

    tk.Tk.__init__ = _tk_init
    tk.Toplevel.__init__ = _toplevel_init
    tk.Toplevel.deiconify = _toplevel_deiconify
    tk.Tk.deiconify = _tk_deiconify
    tk.Menu.tk_popup = lambda self, *args, **kwargs: None

    try:
        from venvdiscover.ui.caches_dialog import CacheCleanDialog, CachesDialog
        dialog_classes = (DrivesDialog, CondaCleanDialog, ConfirmDeleteDialog, SettingsDialog, CachesDialog, CacheCleanDialog)
    except ImportError:
        dialog_classes = (DrivesDialog, CondaCleanDialog, ConfirmDeleteDialog, SettingsDialog)

    for dialog_cls in dialog_classes:
        _wrap_dialog_class(dialog_cls)


def _wrap_dialog_class(cls: type) -> None:
    orig_init = cls.__init__

    def _wrapped_init(self: Any, *args: Any, **kwargs: Any) -> None:
        orig_init(self, *args, **kwargs)
        make_offscreen(self)

    cls.__init__ = _wrapped_init


# Automatically install hooks when tk_support is imported
install_offscreen_hooks()


def create_test_app(
    places: Places,
    drive_roots: list[str] | None = None,
    scanner_factory: Any = None,
    mapped: bool = False,
    **kwargs: Any,
) -> App:
    """Build a test App instance kept invisible (withdrawn or mapped off-screen)."""
    install_offscreen_hooks()
    factory = scanner_factory if scanner_factory is not None else (lambda **kw: DummyScanner())
    app = App(
        places,
        drive_roots=drive_roots,
        scanner_factory=factory,
        **kwargs,
    )
    make_offscreen(app)
    if mapped:
        app.deiconify()
        make_offscreen(app)
        app.update_idletasks()
    else:
        app.withdraw()
    return app


def create_test_root(mapped: bool = False) -> tk.Tk:
    """Build a test tk.Tk instance kept invisible (withdrawn or mapped off-screen)."""
    install_offscreen_hooks()
    root = tk.Tk()
    make_offscreen(root)
    if mapped:
        root.deiconify()
        make_offscreen(root)
        root.update_idletasks()
    else:
        root.withdraw()
    return root


def pump_after(root: tk.Misc) -> None:
    """Execute all pending after timer callbacks immediately without waiting."""
    try:
        timer_ids = list(root.tk.call("after", "info"))
    except Exception:
        return
    for timer_id in timer_ids:
        try:
            info = root.tk.call("after", "info", timer_id)
            script = str(info[0])
            kind = str(info[1]) if len(info) > 1 else ""
            if kind == "timer":
                root.tk.eval(script)
                try:
                    root.after_cancel(timer_id)
                except Exception:
                    pass
        except Exception:
            pass
