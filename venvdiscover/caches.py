"""Discover package manager caches (uv, poetry, pipenv, pip) and safely clean them."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import time
from typing import Callable, Mapping
import uuid

from venvdiscover.model import longpath, norm_key
from venvdiscover.places import Places
from venvdiscover.trashbin import _rmtree_force

# uv bucket directories match <name>-v<N>, e.g. environments-v2, builds-v0, wheels-v5
_UV_BUCKET_RE = re.compile(r"^[a-z0-9_-]+-v[0-9]+$", re.IGNORECASE)

# Safe allowlists for fallback content deletion when tool is uninstalled
# uv: CACHEDIR.TAG is required; top-level entries are bucket folders, lockfiles, or .gitignore
# Sourced from crates/uv-cache/src/lib.rs: create_base_files writes .gitignore ("*"), CACHEDIR.TAG, .lock
_UV_ALLOWED_FILES = {"cachedir.tag", ".lock", "uv.lock", ".gitignore"}
# pip: wheels, http, http-v2, selfcheck, selfcheck.json, CACHEDIR.TAG, .gitignore
# Sourced from pip._internal.cache (WheelCache: 'wheels'), pip._internal.network.cache ('http-v2', 'http'),
# pip._internal.self_outdated_check ('selfcheck.json'), pip._internal.locations.base ('CACHEDIR.TAG')
_PIP_ALLOWED_ENTRIES = {"wheels", "http", "http-v2", "selfcheck", "selfcheck.json", "cachedir.tag", ".gitignore"}
# poetry: artifacts, cache (holds repositories), repositories, virtualenvs, CACHEDIR.TAG, .gitignore
# Sourced from src/poetry/config/config.py:
# repository_cache_directory = Path(cache-dir) / "cache" / "repositories"
# artifacts_cache_directory = Path(cache-dir) / "artifacts"
# virtualenvs_path = Path(cache-dir) / "virtualenvs" (when in_project is false)
_POETRY_ALLOWED_ENTRIES = {"artifacts", "cache", "repositories", "virtualenvs", "cachedir.tag", ".gitignore"}
# pipenv: wheels, pkgs, http, http-v2, pipenv-manifests, cachedir.tag, .gitignore
# Sourced from pipenv's own codebase:
# - 'wheels' and 'pkgs': pipenv/utils/pip.py:94-95 (PIP_WHEEL_DIR / PIP_DESTINATION_DIR);
# - 'http' and 'http-v2': pip's network cache, because pipenv/utils/pip.py:93 sets PIP_CACHE_DIR to the pipenv cache;
# - 'pipenv-manifests': pipenv/resolver/backends/pure_python.py:401;
# - no 'selfcheck', because pipenv/__init__.py:14 sets PIP_DISABLE_PIP_VERSION_CHECK;
# - 'cachedir.tag' and '.gitignore' standard cache directory markers.
_PIPENV_ALLOWED_ENTRIES = {"wheels", "pkgs", "http", "http-v2", "pipenv-manifests", "cachedir.tag", ".gitignore"}


@dataclass
class Cache:
    """A discovered package manager cache folder."""

    tool: str  # "uv", "poetry", "pipenv", "pip"
    path: str
    size: int
    last_modified: float
    clean_method: str  # e.g. "uv cache clean", "pip cache purge", "poetry cache clear", "pipenv --clear", or "safe permanent delete"


def measure_cache(path: str, tool: str = "") -> tuple[int, float]:
    """Calculate total size in bytes and most recent modification time.

    For poetry, excludes any 'virtualenvs' subdirectory.
    """
    total_size = 0
    max_mtime = 0.0
    lp = longpath(path)
    try:
        max_mtime = os.path.getmtime(lp)
    except OSError:
        pass

    try:
        for root, dirs, files in os.walk(lp):
            if tool == "poetry":
                dirs[:] = [d for d in dirs if d.lower() != "virtualenvs"]
            for f in files:
                fp = os.path.join(root, f)
                try:
                    stat = os.stat(fp)
                    total_size += stat.st_size
                    if stat.st_mtime > max_mtime:
                        max_mtime = stat.st_mtime
                except OSError:
                    continue
    except OSError:
        pass

    return total_size, max_mtime


def _pip_ini_cache_dir(appdata: str) -> str | None:
    r"""Read cache-dir from %APPDATA%\pip\pip.ini if it exists."""
    ini_path = os.path.join(appdata, "pip", "pip.ini")
    lp = longpath(ini_path)
    if not os.path.isfile(lp):
        return None
    try:
        cp = configparser.RawConfigParser()
        cp.read(lp, encoding="utf-8")
        for section in cp.sections():
            if cp.has_option(section, "cache-dir"):
                val = cp.get(section, "cache-dir").strip().strip('"\'')
                if val:
                    return os.path.expandvars(os.path.expanduser(val))
    except Exception:
        pass
    return None


def find_caches(
    places: Places,
    environ: Mapping[str, str] | None = None,
) -> list[Cache]:
    """Find existing package manager caches (uv, poetry, pipenv, pip) with their sizes and clean methods."""
    env = environ if environ is not None else os.environ
    path_val = env.get("PATH", "")

    # 1. uv: %LOCALAPPDATA%\uv\cache, override UV_CACHE_DIR
    uv_path = env.get("UV_CACHE_DIR") or os.path.join(places.localappdata, "uv", "cache")

    # 2. poetry: %LOCALAPPDATA%\pypoetry\Cache, override POETRY_CACHE_DIR
    poetry_path = env.get("POETRY_CACHE_DIR") or os.path.join(places.localappdata, "pypoetry", "Cache")

    # 3. pipenv: %LOCALAPPDATA%\pipenv\pipenv\Cache, override PIPENV_CACHE_DIR
    pipenv_path = env.get("PIPENV_CACHE_DIR") or os.path.join(places.localappdata, "pipenv", "pipenv", "Cache")

    # 4. pip: %LOCALAPPDATA%\pip\Cache, override PIP_CACHE_DIR or pip.ini cache-dir
    pip_override = env.get("PIP_CACHE_DIR") or _pip_ini_cache_dir(places.appdata)
    pip_path = pip_override or os.path.join(places.localappdata, "pip", "Cache")

    tools_and_paths = [
        ("uv", uv_path),
        ("poetry", poetry_path),
        ("pipenv", pipenv_path),
        ("pip", pip_path),
    ]

    caches: list[Cache] = []
    seen_paths: set[str] = set()

    for tool, path in tools_and_paths:
        if not path:
            continue
        lp = longpath(path)
        if not os.path.isdir(lp):
            continue

        k = norm_key(path)
        if k in seen_paths:
            continue
        seen_paths.add(k)

        # Check if tool is on PATH
        tool_on_path = shutil.which(tool, path=path_val) is not None
        if tool == "uv":
            method = "uv cache clean" if tool_on_path else "safe permanent delete"
        elif tool == "pip":
            method = "pip cache purge" if tool_on_path else "safe permanent delete"
        elif tool == "pipenv":
            method = "pipenv --clear" if tool_on_path else "safe permanent delete"
        elif tool == "poetry":
            method = "poetry cache clear" if tool_on_path else "safe permanent delete"
        else:
            method = "safe permanent delete"

        size, last_modified = measure_cache(path, tool=tool)
        caches.append(
            Cache(
                tool=tool,
                path=os.path.abspath(path),
                size=size,
                last_modified=last_modified,
                clean_method=method,
            )
        )

    return caches


def check_cache_safety(cache_path: str, tool: str = "") -> None:
    """Refuse to clean if cache_path contains an env or Python install, or is inside one."""
    cp = os.path.abspath(cache_path)
    lp = longpath(cp)

    # 1. Is cache_path inside an env? (any parent contains pyvenv.cfg or conda-meta)
    cur = os.path.dirname(cp)
    while cur and os.path.dirname(cur) != cur:
        lp_cur = longpath(cur)
        if os.path.isfile(os.path.join(lp_cur, "pyvenv.cfg")) or os.path.isdir(os.path.join(lp_cur, "conda-meta")):
            raise ValueError(f"This cache folder is inside an env ({cur}). Cleaning was refused.")
        cur = os.path.dirname(cur)

    # 2. Does cache_path hold an env directly?
    if os.path.isfile(os.path.join(lp, "pyvenv.cfg")) or os.path.isdir(os.path.join(lp, "conda-meta")):
        raise ValueError("This cache folder holds an env. Cleaning was refused.")
    if os.path.isfile(os.path.join(lp, "python.exe")) and os.path.isfile(os.path.join(lp, "Lib", "os.py")):
        raise ValueError("This cache folder holds a Python install. Cleaning was refused.")

    # 3. Does cache_path hold an env or Python install in any subfolder?
    # (excluding virtualenvs for poetry, and excluding uv's bucket folders for uv)
    for root, dirs, files in os.walk(lp):
        if tool == "poetry":
            dirs[:] = [d for d in dirs if d.lower() != "virtualenvs"]
        elif tool == "uv":
            if root == lp or norm_key(root) == norm_key(lp):
                dirs[:] = [d for d in dirs if not _UV_BUCKET_RE.match(d)]
        if "pyvenv.cfg" in files or os.path.isdir(os.path.join(root, "conda-meta")):
            raise ValueError(f"This cache folder holds an env ({root}). Cleaning was refused.")
        if "python.exe" in files and os.path.isfile(os.path.join(root, "Lib", "os.py")):
            raise ValueError(f"This cache folder holds a Python install ({root}). Cleaning was refused.")


def validate_cache_layout_for_fallback(cache_path: str, tool: str) -> str | None:
    """Validate that cache_path contains only known entries for tool before fallback delete."""
    lp = longpath(cache_path)
    try:
        entries = os.listdir(lp)
    except OSError as e:
        return f"Failed to list cache directory: {e}"

    if not entries:
        return None

    if tool == "uv":
        has_tag = any(e.lower() == "cachedir.tag" for e in entries)
        if not has_tag:
            return f"Refusing to delete uv cache: missing required 'CACHEDIR.TAG' in {cache_path}"
        for entry in entries:
            if entry.startswith(".deleting-"):
                continue
            if entry.lower() in _UV_ALLOWED_FILES:
                continue
            if _UV_BUCKET_RE.match(entry):
                continue
            return f"Refusing to delete uv cache: unrecognized entry '{entry}' in {cache_path}"
    elif tool == "pip":
        for entry in entries:
            if entry.startswith(".deleting-"):
                continue
            if entry.lower() in _PIP_ALLOWED_ENTRIES:
                continue
            return f"Refusing to delete pip cache: unrecognized entry '{entry}' in {cache_path}"
    elif tool == "poetry":
        for entry in entries:
            if entry.startswith(".deleting-"):
                continue
            if entry.lower() in _POETRY_ALLOWED_ENTRIES:
                continue
            return f"Refusing to delete poetry cache: unrecognized entry '{entry}' in {cache_path}"
    elif tool == "pipenv":
        for entry in entries:
            if entry.startswith(".deleting-"):
                continue
            if entry.lower() in _PIPENV_ALLOWED_ENTRIES:
                continue
            return f"Refusing to delete pipenv cache: unrecognized entry '{entry}' in {cache_path}"
    else:
        return f"Refusing to delete cache: unknown tool '{tool}'"

    return None


def run_command_streaming(
    cmd: list[str],
    on_line: Callable[[str], None],
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a CLI command with stdout/stderr streamed to on_line."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    proc_env = dict(env or os.environ)
    proc_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        env=proc_env,
    )
    if proc.stdout:
        with proc.stdout:
            for line in proc.stdout:
                on_line(line.rstrip("\r\n"))
    return proc.wait()


def safe_delete_cache_contents(
    cache_path: str,
    tool: str,
    on_line: Callable[[str], None],
) -> int:
    """Delete contents of cache folder using .deleting-* temporary rename pattern."""
    lp_cache = longpath(cache_path)
    if not os.path.isdir(lp_cache):
        on_line("Cache folder does not exist.")
        return 0

    # 1. Cleanup any leftover .deleting-* entries first
    try:
        for entry in os.listdir(lp_cache):
            if entry.startswith(".deleting-"):
                _rmtree_force(longpath(os.path.join(cache_path, entry)))
    except OSError:
        pass

    # 2. Validate allowlist layout per tool before touching anything
    layout_err = validate_cache_layout_for_fallback(cache_path, tool)
    if layout_err:
        on_line(layout_err)
        return 1

    # 3. Read current entries
    try:
        entries = os.listdir(lp_cache)
    except OSError as e:
        on_line(f"Failed to read cache folder: {e}")
        return 1

    if not entries:
        on_line("Cache is empty.")
        return 0

    failed_entries: list[str] = []
    for entry in entries:
        if tool == "poetry" and entry.lower() == "virtualenvs":
            on_line(f"Preserving poetry virtualenvs folder: {entry}")
            continue

        item_path = os.path.join(cache_path, entry)
        lp_item = longpath(item_path)
        deleting_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        deleting_path = os.path.join(cache_path, f".deleting-{deleting_id}")
        lp_deleting = longpath(deleting_path)

        try:
            os.rename(lp_item, lp_deleting)
        except OSError as e:
            on_line(f"Could not rename '{entry}': {e}")
            failed_entries.append(entry)
            continue

        try:
            if os.path.isdir(lp_deleting):
                _rmtree_force(lp_deleting)
            else:
                os.remove(lp_deleting)
            on_line(f"Deleted {entry}")
        except Exception as e:
            on_line(f"Could not remove '{entry}': {e}")
            failed_entries.append(entry)

    if failed_entries:
        on_line(
            f"Cache cleanup incomplete: failed to remove {len(failed_entries)} entries "
            "(they may be in use by another process)."
        )
        return 1

    on_line("Cache cleanup finished.")
    return 0


def clean_cache(
    cache: Cache,
    on_line: Callable[[str], None],
    environ: Mapping[str, str] | None = None,
) -> int:
    """Clean a package manager cache using its CLI if available, or safe deletion fallback."""
    env_map = environ if environ is not None else os.environ
    path_val = env_map.get("PATH", "")

    # Safety check: refuse if cache holds or is inside an env
    check_cache_safety(cache.path, tool=cache.tool)

    tool_exe = shutil.which(cache.tool, path=path_val)
    if tool_exe:
        on_line(f"Cleaning {cache.tool} cache using {tool_exe}...")
        if cache.tool == "uv":
            return run_command_streaming([tool_exe, "cache", "clean"], on_line, env_map)
        elif cache.tool == "pip":
            return run_command_streaming([tool_exe, "cache", "purge"], on_line, env_map)
        elif cache.tool == "pipenv":
            return run_command_streaming([tool_exe, "--clear"], on_line, env_map)
        elif cache.tool == "poetry":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
            proc = subprocess.run(
                [tool_exe, "cache", "list"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
                env={**env_map, "PYTHONIOENCODING": "utf-8"},
            )
            repos = [
                line.strip()
                for line in proc.stdout.splitlines()
                if line.strip() and not line.strip().lower().startswith(("no ", "there are no"))
            ]
            exit_code = 0
            if repos:
                for repo in repos:
                    on_line(f"Clearing poetry cache for repository: {repo}")
                    ec = run_command_streaming([tool_exe, "cache", "clear", "--all", "-n", repo], on_line, env_map)
                    if ec != 0:
                        exit_code = ec
            else:
                on_line("Clearing poetry cache...")
                exit_code = run_command_streaming([tool_exe, "cache", "clear", "--all", "-n", "all"], on_line, env_map)
            return exit_code
        else:
            on_line(f"Unknown tool: {cache.tool}")
            return 1
    else:
        on_line(f"'{cache.tool}' is not on PATH. Safely deleting cache contents...")
        return safe_delete_cache_contents(cache.path, tool=cache.tool, on_line=on_line)
