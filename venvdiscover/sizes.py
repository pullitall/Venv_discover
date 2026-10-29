"""Measure folder sizes and determine last used timestamp of environments."""

from __future__ import annotations

import os
from collections.abc import Callable, Collection

from venvdiscover.model import Env, longpath
from venvdiscover.walker import _is_junction


def folder_size(
    path: str,
    should_stop: Callable[[], bool] = lambda: False,
    skip_dirs: Collection[str] = (),
) -> int:
    """The sum of st_size over files, no symlinks or junctions, through longpath."""
    total = 0
    stack: list[tuple[str, bool]] = [(path, True)]
    skip_names = {s.lower() for s in skip_dirs}

    while stack:
        if should_stop():
            break
        current, is_root = stack.pop()
        try:
            with os.scandir(longpath(current)) as it:
                for entry in it:
                    try:
                        if entry.is_symlink() or _is_junction(entry):
                            continue
                        if entry.is_dir():
                            if is_root and entry.name.lower() in skip_names:
                                continue
                            stack.append((entry.path, False))
                        elif entry.is_file():
                            total += entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue

    return total


def last_used(env: Env) -> float | None:
    """Return max timestamp among python.exe atime, history/site-packages/pyvenv.cfg mtimes, or None."""
    times: list[float] = []

    # atime of python.exe / Scripts\python.exe
    for rel in ("python.exe", os.path.join("Scripts", "python.exe")):
        p = os.path.join(env.path, rel)
        try:
            st = os.stat(longpath(p))
            times.append(st.st_atime)
        except OSError:
            pass

    # mtime of conda-meta\history, Lib\site-packages and pyvenv.cfg
    for rel in (
        os.path.join("conda-meta", "history"),
        os.path.join("Lib", "site-packages"),
        "pyvenv.cfg",
    ):
        p = os.path.join(env.path, rel)
        try:
            st = os.stat(longpath(p))
            times.append(st.st_mtime)
        except OSError:
            pass

    return max(times) if times else None
