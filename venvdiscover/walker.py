"""Drive enumeration and junction-safe filesystem walk."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Collection
from dataclasses import dataclass

from venvdiscover.classify import probe
from venvdiscover.model import Env, Kind, _strip_long_prefix, longpath, norm_key
from venvdiscover.places import Places


@dataclass
class Drive:
    root: str
    fs: str
    label: str


def local_drives() -> list[Drive]:
    """Return local fixed drives with roots formatted with trailing backslash (e.g. 'C:\\')."""
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_unicode_buffer(512)
    n = kernel32.GetLogicalDriveStringsW(512, buf)
    if not n:
        return []

    raw_drives = [d for d in buf[:n].split("\x00") if d]
    result: list[Drive] = []

    for d in raw_drives:
        # Guarantee trailing backslash
        root = d if d.endswith("\\") else d + "\\"
        try:
            drive_type = kernel32.GetDriveTypeW(root)
            if drive_type == 3:  # DRIVE_FIXED
                vol_buf = ctypes.create_unicode_buffer(261)
                fs_buf = ctypes.create_unicode_buffer(261)
                res = kernel32.GetVolumeInformationW(root, vol_buf, 261, None, None, None, fs_buf, 261)
                if res:
                    result.append(Drive(root=root, fs=fs_buf.value, label=vol_buf.value))
        except Exception:
            continue

    return result


def default_scan_drives(drives: list[Drive]) -> list[str]:
    """Return drive roots whose filesystem is NTFS or ReFS."""
    return [d.root for d in drives if d.fs.upper() in ("NTFS", "REFS")]


SKIP_NAMES = frozenset({
    "$recycle.bin",
    "system volume information",
    "node_modules",
    ".git",
    "__pycache__",
    "site-packages",
    ".venvdiscover-bin",
})


def _is_junction(entry: os.DirEntry) -> bool:
    if hasattr(entry, "is_junction"):
        try:
            return entry.is_junction()
        except OSError:
            return False
    try:
        st = entry.stat(follow_symlinks=False)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & 0x0400)
    except OSError:
        return False


def _is_windows_under_drive_root(path: str, parent: str) -> bool:
    """True only if path is named 'windows' directly under a drive root."""
    name = os.path.basename(path).lower()
    if name != "windows":
        return False
    drive, rest = os.path.splitdrive(parent)
    return bool(drive and rest.strip("\\/") == "")


def walk(
    root: str,
    places: Places,
    on_env: Callable[[Env], None],
    should_stop: Callable[[], bool],
    skip_paths: Collection[str] = (),
    on_progress: Callable[[int, str], None] | None = None,
) -> int:
    """Walk root using an explicit stack, discovering venvs and conda roots without entering them."""
    # Ensure root has trailing backslash if it is a bare drive root and strip long prefix
    root = _strip_long_prefix(root)
    drive, rest = os.path.splitdrive(root)
    if drive and rest == "":
        root = root + "\\"

    skip_keys = set()
    for p in skip_paths:
        skip_keys.add(norm_key(p))
        skip_keys.add(os.path.normcase(os.path.abspath(p)))

    visited = 0
    stack: list[str] = [root]

    while stack:
        if should_stop():
            break

        current = _strip_long_prefix(stack.pop())
        visited += 1

        if on_progress is not None and visited % 2000 == 0:
            on_progress(visited, current)

        try:
            with os.scandir(longpath(current)) as it:
                entry_names: set[str] = set()
                child_dirs: list[str] = []

                for entry in it:
                    try:
                        if entry.is_symlink() or _is_junction(entry):
                            continue

                        name_lower = entry.name.lower()
                        entry_names.add(name_lower)

                        if entry.is_dir():
                            plain_path = _strip_long_prefix(entry.path)
                            if name_lower in SKIP_NAMES:
                                continue
                            if _is_windows_under_drive_root(plain_path, current):
                                continue
                            if skip_keys and os.path.normcase(plain_path) in skip_keys:
                                continue
                            child_dirs.append(plain_path)
                    except OSError:
                        continue

            if "pyvenv.cfg" in entry_names or "conda-meta" in entry_names:
                env = probe(current, places)
                if env is not None:
                    on_env(env)
                    if env.kind == Kind.CONDA_BASE:
                        envs_child = os.path.join(current, "envs")
                        try:
                            if os.path.isdir(longpath(envs_child)):
                                if not skip_keys or os.path.normcase(envs_child) not in skip_keys:
                                    stack.append(envs_child)
                        except OSError:
                            pass
                    # Don't descend into other children of the found env
                    continue

            # Push children in order
            stack.extend(child_dirs)

        except OSError:
            continue

    return visited
