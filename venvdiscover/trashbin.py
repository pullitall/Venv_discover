"""14-day bin: move, restore, permanent delete, purge, and leftover cleanup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import math
import os
import re
import shutil
import stat
import time
import uuid

from venvdiscover import connections
from venvdiscover.model import (
    Env,
    Kind,
    LOCKED_KINDS,
    longpath,
    norm_key,
)
from venvdiscover.places import Places

BIN_LIMIT = 1024 ** 3  # 1 GB in bytes; > BIN_LIMIT skips the bin
KEEP_DAYS = 14

_RENAME_REFUSED_MSG = (
    "This env is in use, or Windows needs admin rights to move it. "
    "Close programs using it and try again. Nothing was changed."
)


class DeleteError(Exception):
    """Plain-language message for the UI."""
    pass


class LockedError(DeleteError):
    pass


@dataclass
class BinItem:
    id: str
    bin_root: str
    original_path: str
    kind: Kind
    name: str
    size: int
    deleted_at: float
    conda_root: str | None
    had_env_line: bool
    moved: list[tuple[str, str]]

    @property
    def env_path(self) -> str:
        return os.path.join(self.bin_root, self.id, "env")

    def days_left(self, now: float) -> int:
        return max(0, math.ceil((self.deleted_at + KEEP_DAYS * 86400 - now) / 86400))


def bin_root_for(env_path: str, places: Places) -> str:
    """Pure function returning the bin directory path for an env without creating it."""
    drive_env = os.path.splitdrive(os.path.abspath(env_path))[0]
    drive_local = os.path.splitdrive(os.path.abspath(places.localappdata))[0]

    if drive_env.upper() == drive_local.upper():
        return os.path.join(places.app_dir, "bin")
    return os.path.join(f"{drive_env}\\", ".venvdiscover-bin")


def _ensure_bin_root(bin_root: str) -> None:
    lp = longpath(bin_root)
    if not os.path.exists(lp):
        os.makedirs(lp, exist_ok=True)
        _, rest = os.path.splitdrive(bin_root)
        if rest.strip("\\/").lower() == ".venvdiscover-bin":
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(longpath(bin_root), 0x02)
            except Exception:
                pass


def project_marker(env_path: str) -> str | None:
    """Return the first project marker entry name found (e.g. '.git', 'main.py'), or None."""
    lp_env = longpath(env_path)
    try:
        with os.scandir(lp_env) as it:
            for entry in it:
                name_lower = entry.name.lower()
                if entry.is_dir():
                    if name_lower in {".git", ".hg", ".svn"}:
                        return entry.name
                elif entry.is_file():
                    if name_lower in {"pyproject.toml", "setup.py", "setup.cfg"}:
                        return entry.name
                    if name_lower.startswith("requirements") and name_lower.endswith(".txt"):
                        return entry.name
                    if name_lower.endswith(".py") or name_lower.endswith(".ipynb"):
                        return entry.name
    except OSError:
        pass
    return None


def _check_project_root(env_path: str) -> None:
    """Refuse to delete if the env folder is also a project root."""
    marker = project_marker(env_path)
    if marker:
        raise DeleteError(
            f"This folder is also a project ({marker}). "
            "Only the env inside it could be deleted, so nothing was changed."
        )


def _rmtree_force(path: str) -> None:
    lp = longpath(path)
    if not os.path.exists(lp):
        return
    if os.path.isfile(lp) or os.path.islink(lp):
        try:
            os.chmod(lp, stat.S_IWRITE)
            os.unlink(lp)
        except Exception:
            pass
        return

    def _on_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    try:
        shutil.rmtree(lp, onexc=_on_error)
    except TypeError:
        shutil.rmtree(lp, onerror=_on_error)


def plan_delete(envs: list[Env]) -> tuple[list[Env], list[Env], list[Env]]:
    """Split envs into (to_bin, permanent, locked). Raises ValueError if any unlocked env has size None."""
    to_bin: list[Env] = []
    permanent: list[Env] = []
    locked: list[Env] = []

    for env in envs:
        if env.locked:
            locked.append(env)
        else:
            if env.size is None:
                raise ValueError(f"Environment '{env.name}' has not been measured yet.")
            if env.size > BIN_LIMIT:
                permanent.append(env)
            else:
                to_bin.append(env)

    return to_bin, permanent, locked


def move_to_bin(
    env: Env,
    places: Places,
    now: float,
    log: Callable[[str, str, str], None],
) -> BinItem:
    """Move env to bin directory. Handles connections best-effort after rename succeeds."""
    if env.locked:
        raise LockedError(f"Cannot delete locked environment '{env.name}'.")

    lp_env = longpath(env.path)
    if not os.path.isdir(lp_env):
        raise DeleteError("This env isn't there any more. The list will refresh.")

    _check_project_root(env.path)

    bin_root = bin_root_for(env.path, places)
    _ensure_bin_root(bin_root)

    safe_name = re.sub(r"[^\w.-]+", "_", env.name)[:40]
    item_id = f"{int(now)}-{safe_name}"
    item_dir = os.path.join(bin_root, item_id)
    suffix = 1
    while os.path.exists(longpath(item_dir)):
        suffix += 1
        item_id = f"{int(now)}-{safe_name}-{suffix}"
        item_dir = os.path.join(bin_root, item_id)

    os.makedirs(longpath(item_dir), exist_ok=True)
    dest_env = os.path.join(item_dir, "env")

    try:
        os.rename(lp_env, longpath(dest_env))
    except OSError as e:
        try:
            os.rmdir(longpath(item_dir))
        except Exception:
            pass
        raise DeleteError(_RENAME_REFUSED_MSG) from e

    had_env_line = False
    if env.kind in (Kind.CONDA, Kind.CONDA_BASE) or env.conda_root:
        had_env_line = connections.remove_env_line(places.environments_txt, env.path)

    moved_extras: list[tuple[str, str]] = []
    extras_dir = os.path.join(item_dir, "extras")

    kernels = connections.find_kernels(env.path, connections.kernel_dirs(places))
    shortcuts = connections.find_shortcuts(env.path, connections.shortcut_dirs(places))
    extra_paths = kernels + shortcuts

    for idx, extra_path in enumerate(extra_paths):
        extra_base = os.path.basename(extra_path)
        extra_dest_dir = os.path.join(extras_dir, str(idx))
        os.makedirs(longpath(extra_dest_dir), exist_ok=True)
        extra_dest = os.path.join(extra_dest_dir, extra_base)
        try:
            shutil.move(longpath(extra_path), longpath(extra_dest))
            moved_extras.append((extra_path, extra_dest))
        except Exception as ex:
            log("warning", extra_path, f"Failed to move extra: {ex}")

    item = BinItem(
        id=item_id,
        bin_root=bin_root,
        original_path=env.path,
        kind=env.kind,
        name=env.name,
        size=env.size or 0,
        deleted_at=now,
        conda_root=env.conda_root,
        had_env_line=had_env_line,
        moved=moved_extras,
    )

    item_json_path = os.path.join(item_dir, "item.json")
    with open(longpath(item_json_path), "w", encoding="utf-8") as f:
        json.dump({
            "id": item.id,
            "bin_root": item.bin_root,
            "original_path": item.original_path,
            "kind": item.kind.value,
            "name": item.name,
            "size": item.size,
            "deleted_at": item.deleted_at,
            "conda_root": item.conda_root,
            "had_env_line": item.had_env_line,
            "moved": item.moved,
        }, f, indent=2)

    log("bin", env.path, f"binned to {item_id}")
    return item


def delete_permanently(
    env: Env,
    places: Places,
    log: Callable[[str, str, str], None],
) -> None:
    """Permanently delete an env via rename to .deleting-<id> first, then rmtree."""
    if env.locked:
        raise LockedError(f"Cannot delete locked environment '{env.name}'.")

    lp_env = longpath(env.path)
    if not os.path.isdir(lp_env):
        raise DeleteError("This env isn't there any more. The list will refresh.")

    _check_project_root(env.path)

    bin_root = bin_root_for(env.path, places)
    _ensure_bin_root(bin_root)

    deleting_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    deleting_dir = os.path.join(bin_root, f".deleting-{deleting_id}")

    try:
        os.rename(lp_env, longpath(deleting_dir))
    except OSError as e:
        raise DeleteError(_RENAME_REFUSED_MSG) from e

    if env.kind in (Kind.CONDA, Kind.CONDA_BASE) or env.conda_root:
        connections.remove_env_line(places.environments_txt, env.path)

    kernels = connections.find_kernels(env.path, connections.kernel_dirs(places))
    shortcuts = connections.find_shortcuts(env.path, connections.shortcut_dirs(places))
    for extra_path in kernels + shortcuts:
        lp_extra = longpath(extra_path)
        try:
            _rmtree_force(lp_extra)
        except Exception as ex:
            log("warning", extra_path, f"Failed to delete extra: {ex}")

    _rmtree_force(deleting_dir)
    if os.path.exists(longpath(deleting_dir)):
        log("error", env.path, "failed to remove some files in deleting folder")
        raise DeleteError("Some files couldn't be removed; they'll be cleaned up next start")

    log("delete", env.path, "permanently deleted")


def list_bin(roots: list[str]) -> list[BinItem]:
    """Read all item.json files under provided bin roots, skipping damaged ones."""
    items: list[BinItem] = []
    for root in roots:
        lp_root = longpath(root)
        if not os.path.isdir(lp_root):
            continue
        try:
            with os.scandir(lp_root) as it:
                for entry in it:
                    if not entry.is_dir() or entry.name.startswith(".deleting-"):
                        continue
                    item_json = os.path.join(entry.path, "item.json")
                    lp_json = longpath(item_json)
                    if not os.path.isfile(lp_json):
                        continue
                    try:
                        with open(lp_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        items.append(BinItem(
                            id=str(data["id"]),
                            bin_root=str(data["bin_root"]),
                            original_path=str(data["original_path"]),
                            kind=Kind(data["kind"]),
                            name=str(data["name"]),
                            size=int(data["size"]),
                            deleted_at=float(data["deleted_at"]),
                            conda_root=data.get("conda_root"),
                            had_env_line=bool(data.get("had_env_line", False)),
                            moved=[(str(p[0]), str(p[1])) for p in data.get("moved", [])],
                        ))
                    except Exception:
                        continue
        except OSError:
            continue

    return items


def bin_roots(places: Places, drive_roots: list[str]) -> list[str]:
    """Return paths of existing bin roots only."""
    results: list[str] = []
    seen: set[str] = set()

    app_bin = os.path.join(places.app_dir, "bin")
    if os.path.isdir(longpath(app_bin)):
        seen.add(norm_key(app_bin))
        results.append(app_bin)

    for drive in drive_roots:
        drive_letter = os.path.splitdrive(os.path.abspath(drive))[0]
        root_cand = os.path.join(f"{drive_letter}\\", ".venvdiscover-bin")
        if os.path.isdir(longpath(root_cand)):
            k = norm_key(root_cand)
            if k not in seen:
                seen.add(k)
                results.append(root_cand)

    return results


def restore(item: BinItem, places: Places, log: Callable[[str, str, str], None]) -> None:
    """Restore an item from the bin back to its original location."""
    lp_orig = longpath(item.original_path)
    if os.path.exists(lp_orig):
        raise DeleteError(f"Something is already at {item.original_path}. Nothing was changed.")

    lp_bin_env = longpath(item.env_path)
    if not os.path.isdir(lp_bin_env):
        raise DeleteError("This bin item is damaged. Nothing was changed.")

    parent_dir = os.path.dirname(lp_orig)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    try:
        os.rename(lp_bin_env, lp_orig)
    except OSError as e:
        raise DeleteError(f"Failed to restore {item.original_path}: {e}") from e

    for orig_path, stored_path in item.moved:
        lp_extra_orig = longpath(orig_path)
        lp_extra_stored = longpath(stored_path)
        if os.path.exists(lp_extra_orig):
            log("warning", orig_path, "Original path taken, cannot restore extra")
            continue
        if os.path.exists(lp_extra_stored):
            try:
                os.makedirs(os.path.dirname(lp_extra_orig), exist_ok=True)
                shutil.move(lp_extra_stored, lp_extra_orig)
            except Exception as ex:
                log("warning", orig_path, f"Failed to restore extra: {ex}")

    if item.had_env_line:
        connections.add_env_line(places.environments_txt, item.original_path)

    item_dir = os.path.join(item.bin_root, item.id)
    _rmtree_force(item_dir)
    log("restore", item.original_path, f"restored from {item.id}")


def purge(item: BinItem, log: Callable[[str, str, str], None]) -> None:
    """Permanently delete a bin item folder."""
    item_dir = os.path.join(item.bin_root, item.id)
    _rmtree_force(item_dir)
    log("purge", item.original_path, f"purged from {item.id}")


def purge_expired(
    items: list[BinItem],
    now: float,
    log: Callable[[str, str, str], None],
) -> list[BinItem]:
    """Purge items older than KEEP_DAYS (14 days). Returns list of purged items."""
    purged: list[BinItem] = []
    for item in items:
        if now - item.deleted_at > KEEP_DAYS * 86400:
            purge(item, log)
            purged.append(item)
    return purged


def cleanup_leftovers(roots: list[str]) -> int:
    """Remove any orphaned .deleting-* folders in the provided bin roots."""
    cleaned = 0
    for root in roots:
        lp_root = longpath(root)
        if not os.path.isdir(lp_root):
            continue
        try:
            with os.scandir(lp_root) as it:
                for entry in it:
                    if entry.is_dir() and entry.name.startswith(".deleting-"):
                        _rmtree_force(entry.path)
                        cleaned += 1
        except OSError:
            continue
    return cleaned
