"""Measure folder sizes and determine last used timestamp of environments."""

from __future__ import annotations

import os
from collections.abc import Callable, Collection
from dataclasses import dataclass

from venvdiscover.model import Env, longpath
from venvdiscover.walker import _is_junction

# Files smaller than this are counted as the folder's own without reading their link count. Reading it needs a
# full os.stat per file (DirEntry.stat() leaves st_nlink at 0 on Windows); measured on real conda envs, this
# threshold skips ~60% of those calls and keeps "frees" within 1% of exact.
LINK_CHECK_MIN = 4096


@dataclass(frozen=True)
class FolderUsage:
    total: int  # the sum of st_size over files: the size on disk
    unique: int  # bytes only this folder holds: deleting it frees about this much


def folder_usage(
    path: str,
    should_stop: Callable[[], bool] = lambda: False,
    skip_dirs: Collection[str] = (),
    check_links: bool = True,
) -> FolderUsage:
    """Size on disk and the part only this folder holds, no symlinks or junctions, through longpath.

    conda, mamba, pixi and uv hard-link package files from their caches into envs, so part of an env's size is
    shared and stays on disk when the env is deleted. A file counts as the folder's own when all its hard links
    are inside the folder (a file linked twice inside counts once).
    """
    total = 0
    unique = 0
    linked: dict[tuple[int, int], list[int]] = {}  # (st_dev, st_ino) -> [size, st_nlink, links seen here]
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
                            size = entry.stat().st_size
                            total += size
                            if not check_links or size < LINK_CHECK_MIN:
                                unique += size
                                continue
                            try:
                                st = os.stat(entry.path)
                            except OSError:
                                unique += size
                                continue
                            if st.st_nlink <= 1:
                                unique += size
                            else:
                                linked.setdefault((st.st_dev, st.st_ino), [size, st.st_nlink, 0])[2] += 1
                    except OSError:
                        continue
        except OSError:
            continue

    unique += sum(size for size, nlink, seen in linked.values() if seen >= nlink)
    return FolderUsage(total=total, unique=unique)


def folder_size(
    path: str,
    should_stop: Callable[[], bool] = lambda: False,
    skip_dirs: Collection[str] = (),
) -> int:
    """The sum of st_size over files, no symlinks or junctions, through longpath."""
    return folder_usage(path, should_stop, skip_dirs, check_links=False).total


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
