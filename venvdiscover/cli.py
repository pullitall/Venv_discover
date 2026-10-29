"""Command-line interface for Venv Discover.

Provides both human-readable and structured JSON output for scripts and AI agents.
Read-only: this module never deletes anything and never modifies settings.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import datetime
import io
import json
import os
import queue
import re
import sys
import time

from venvdiscover import __version__, connections, packages, sizes, store
from venvdiscover.model import (
    Env,
    Kind,
    Package,
    _strip_long_prefix,
    is_under,
    longpath,
    norm_key,
    normalize_name,
)
from venvdiscover.places import Places
from venvdiscover.scanner import (
    EnvFound,
    EnvGone,
    EnvUpdated,
    Progress,
    ScanDone,
    Scanner,
    SizesDone,
)

_STREAM_DEFAULT = object()
_SENTINEL = object()


def _safe_write(stream: io.TextIOBase | None, msg: str) -> None:
    """Write to stream and flush safely if stream is not None."""
    if stream is not None:
        try:
            stream.write(msg)
            stream.flush()
        except Exception:
            pass


def fmt_size(n: int | None) -> str:
    """Format byte count into human-readable string: None -> '—', else 1024-based with 1 decimal."""
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"

    units = ["KB", "MB", "GB", "TB"]
    val = n / 1024.0
    unit_idx = 0

    while unit_idx < len(units) - 1 and round(val, 1) >= 1024.0:
        val /= 1024.0
        unit_idx += 1

    return f"{val:.1f} {units[unit_idx]}"


def fmt_date(ts: float | None) -> str:
    """Format epoch timestamp as 'YYYY-MM-DD', or '—' if None or non-positive."""
    if ts is None or ts <= 0:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return "—"


def parse_search_query(q: str) -> tuple[str, str | None]:
    """Parse 'NAME[==VER]' into (normalized_name, exact_version_or_None)."""
    s = q.strip()
    if "==" in s:
        name, ver = s.split("==", 1)
        return normalize_name(name.strip()), ver.strip()
    return normalize_name(s), None


def env_matches_search(env: Env, target_name: str, target_ver: str | None) -> bool:
    """Return True if env has a package matching the normalized name and optional version."""
    for pkg in env.packages:
        if normalize_name(pkg.name) == target_name:
            if target_ver is None or pkg.version == target_ver:
                return True
    return False


def build_connections(
    env_path: str,
    places: Places,
    env_txt_keys: set[str],
    kdirs: list[str],
    sdirs: list[str],
) -> dict:
    """Build connections mapping for an environment: environments_txt, kernels, and shortcuts."""
    is_in_txt = norm_key(env_path) in env_txt_keys
    raw_kernels = connections.find_kernels(env_path, kdirs)
    raw_shortcuts = connections.find_shortcuts(env_path, sdirs)
    kernels = sorted(_strip_long_prefix(k) for k in raw_kernels)
    shortcuts = sorted(_strip_long_prefix(s) for s in raw_shortcuts)
    return {
        "environments_txt": is_in_txt,
        "kernels": kernels,
        "shortcuts": shortcuts,
    }


def build_json(
    envs: list[Env],
    places: Places,
    args: argparse.Namespace,
    environ: Mapping[str, str],
    scanned_at: datetime.datetime,
) -> dict:
    """Build the stable, versioned JSON document matching schema version 1."""
    env_txt_keys: set[str] = set()
    read_res = connections._read_env_lines(places.environments_txt)
    if read_res is not None:
        for line in read_res[0]:
            s = line.strip()
            if s:
                env_txt_keys.add(norm_key(s))

    kdirs = connections.kernel_dirs(places)
    sdirs = connections.shortcut_dirs(places)

    out_envs = []
    for env in envs:
        clean_path = _strip_long_prefix(os.path.abspath(env.path))
        size_val = int(env.size) if (args.sizes and env.size is not None) else None

        if args.sizes and env.last_used is not None and env.last_used > 0:
            last_used_val = datetime.datetime.fromtimestamp(
                env.last_used, datetime.timezone.utc
            ).isoformat()
        else:
            last_used_val = None

        conda_root_val = (
            _strip_long_prefix(os.path.abspath(env.conda_root))
            if env.conda_root
            else None
        )

        python_val = env.python_version if env.python_version else None

        if args.packages:
            sorted_pkgs = sorted(env.packages, key=lambda p: p.name.lower())
            packages_val = [
                {"name": p.name, "version": p.version, "source": p.source}
                for p in sorted_pkgs
            ]
        else:
            packages_val = None

        conns_val = build_connections(env.path, places, env_txt_keys, kdirs, sdirs)

        out_envs.append({
            "path": clean_path,
            "name": env.name,
            "kind": env.kind.value,
            "locked": env.locked,
            "python": python_val,
            "size": size_val,
            "last_used": last_used_val,
            "conda_root": conda_root_val,
            "packages": packages_val,
            "connections": conns_val,
        })

    doc: dict = {
        "schema": 1,
        "app": __version__,
        "scanned_at": scanned_at.isoformat(),
        "envs": out_envs,
    }

    if args.caches:
        try:
            from venvdiscover import caches  # type: ignore[import-not-found]

            found_caches = caches.find_caches(places, environ)
            doc["caches"] = [
                {
                    "tool": c.tool,
                    "path": _strip_long_prefix(os.path.abspath(c.path)),
                    "size": c.size,
                }
                for c in found_caches
            ]
        except (ImportError, AttributeError):
            pass  # no caches support: omit the key

    return doc


def format_table(envs: list[Env], args: argparse.Namespace) -> str:
    """Format environments into a clean, aligned human-readable table."""
    headers = ["Name", "Kind", "Python", "Packages", "Size", "Last used", "Location"]
    rows = []
    for env in envs:
        clean_path = _strip_long_prefix(os.path.abspath(env.path))
        py_str = env.python_version or "—"
        pkg_str = str(len(env.packages)) if env.packages else "—"
        size_str = fmt_size(env.size) if (args.sizes and env.size is not None) else "—"
        date_str = fmt_date(env.last_used) if (args.sizes and env.last_used) else "—"
        rows.append([
            env.name,
            env.kind.value,
            py_str,
            pkg_str,
            size_str,
            date_str,
            clean_path,
        ])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    lines = []
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))

    for row in rows:
        line = "  ".join(row[i].ljust(widths[i]) for i in range(len(headers)))
        lines.append(line)

    lines.append("")
    lines.append(f"Found {len(envs)} environment{'s' if len(envs) != 1 else ''}.")
    return "\n".join(lines)


class CLIArgumentParser(argparse.ArgumentParser):
    """Custom parser that routes errors to stderr stream and exits with code 2 without printing help."""

    def __init__(self, *args, stderr_stream: io.TextIOBase | None = _SENTINEL, **kwargs) -> None:  # type: ignore[assignment]
        super().__init__(*args, **kwargs)
        self._stderr_stream = sys.stderr if stderr_stream is _SENTINEL else stderr_stream

    def print_usage(self, file: io.TextIOBase | None = None) -> None:
        target = self._stderr_stream if file is None or file is sys.stderr else file
        if target is not None:
            self._print_message(self.format_usage(), target)

    def print_help(self, file: io.TextIOBase | None = None) -> None:
        if file is None:
            file = sys.stdout
        if file is not None:
            self._print_message(self.format_help(), file)

    def _print_message(self, message: str, file: io.TextIOBase | None = None) -> None:
        if message:
            target = self._stderr_stream if file is None or file is sys.stderr else file
            if target is not None:
                _safe_write(target, message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            target = self._stderr_stream
            if target is not None:
                _safe_write(target, message)
        raise SystemExit(status)


def create_parser(stderr_stream: io.TextIOBase | None = _SENTINEL) -> argparse.ArgumentParser:  # type: ignore[assignment]
    """Construct argument parser for Venv Discover."""
    parser = CLIArgumentParser(
        prog="VenvDiscover.exe",
        description="Find, inspect and safely report Python environments.",
        stderr_stream=stderr_stream,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="output environments in stable JSON format to stdout",
    )
    parser.add_argument(
        "--packages",
        action="store_true",
        help="include installed package lists",
    )
    parser.add_argument(
        "--sizes",
        action="store_true",
        help="measure disk sizes and last used dates (slower)",
    )
    parser.add_argument(
        "--search",
        type=str,
        default=None,
        metavar="NAME[==VER]",
        help="only envs that have the package (PEP 503 name, optional exact version)",
    )
    parser.add_argument(
        "--known-only",
        action="store_true",
        dest="known_only",
        help="skip the drive walk and scan only known environment locations",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        type=str,
        default=None,
        metavar="DIR",
        help="walk only the specified directories instead of entire drives",
    )
    parser.add_argument(
        "--caches",
        action="store_true",
        help="include package-manager cache locations (uv, poetry, pipenv, pip)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    places: Places | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: io.TextIOBase | None | object = _STREAM_DEFAULT,
    stderr: io.TextIOBase | None | object = _STREAM_DEFAULT,
    scanner_factory: Callable | None = None,
) -> int:
    """Main CLI entry point. Returns exit code: 0 OK, 2 bad arguments, 3 scan error, 4 no stdout."""
    if stdout is _STREAM_DEFAULT:
        stdout = sys.stdout
    if stderr is _STREAM_DEFAULT:
        stderr = sys.stderr

    if stdout is None:
        return 4

    # Reconfigure streams with errors="replace" so unencodable characters do not fail
    for s in (stdout, stderr):
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(errors="replace")
            except Exception:
                pass

    if argv is None:
        argv = sys.argv[1:]
    if environ is None:
        environ = os.environ
    if places is None:
        places = Places.from_environ(environ=environ)
    if scanner_factory is None:
        scanner_factory = Scanner

    try:
        parser = create_parser(stderr_stream=stderr)
        args = parser.parse_args(argv)

        # Argument validations
        if args.known_only and args.paths:
            _safe_write(stderr, "error: --known-only and --paths cannot be used together\n")
            return 2

        if args.paths:
            for p in args.paths:
                if not os.path.isdir(p):
                    _safe_write(stderr, f"error: directory does not exist: {p}\n")
                    return 2

        if args.search is not None:
            raw_s = args.search.strip()
            if not raw_s:
                _safe_write(stderr, "error: --search requires a non-empty package query\n")
                return 2
            if "==" in raw_s:
                pkg_name, pkg_ver = raw_s.split("==", 1)
                if not pkg_name.strip() or not pkg_ver.strip():
                    _safe_write(stderr, "error: --search NAME==VER requires both name and version\n")
                    return 2

        # Determine scan roots
        if args.known_only:
            roots: list[str] | None = []
        elif args.paths:
            roots = [os.path.abspath(p) for p in args.paths]
        else:
            roots = None

        settings = store.load_settings(places.settings_path)
        cached = store.load_cache(places.cache_path)

        event_queue: queue.Queue = queue.Queue()
        measure_sizes = bool(args.sizes)

        try:
            scanner_kwargs: dict = {
                "places": places,
                "settings": settings,
                "cached": cached,
                "out": event_queue,
                "environ": environ,
                "roots": roots,
                "measure_sizes": measure_sizes,
            }
            if args.paths is not None:
                scanner_kwargs["registry_paths"] = lambda: []
            scanner = scanner_factory(**scanner_kwargs)
            scanner.start()
        except Exception as exc:
            _safe_write(stderr, f"scan error: {exc}\n")
            return 3

        discovered_keys: set[str] = set()
        envs_by_key: dict[str, Env] = {}
        scan_finished = False
        sizes_finished = not measure_sizes
        is_tty = getattr(stderr, "isatty", lambda: False)() if stderr is not None else False

        try:
            while not (scan_finished and sizes_finished):
                try:
                    ev = event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if isinstance(ev, Progress):
                    if is_tty:
                        _safe_write(stderr, f"\r{ev.text}\x1b[K")
                    else:
                        _safe_write(stderr, f"{ev.text}\n")
                elif isinstance(ev, EnvFound):
                    discovered_keys.add(ev.env.key)
                    envs_by_key[ev.env.key] = ev.env
                elif isinstance(ev, EnvUpdated):
                    envs_by_key[ev.env.key] = ev.env
                elif isinstance(ev, EnvGone):
                    envs_by_key.pop(ev.key, None)
                elif isinstance(ev, ScanDone):
                    scan_finished = True
                elif isinstance(ev, SizesDone):
                    sizes_finished = True
        except Exception as exc:
            scanner.stop()
            _safe_write(stderr, f"scan error: {exc}\n")
            return 3

        # Clear progress indicator from stderr only on tty
        if is_tty:
            _safe_write(stderr, "\r\x1b[K")

        # Determine final envs list
        if args.paths is not None:
            result_envs = [
                e for e in envs_by_key.values()
                if any(is_under(e.path, p) for p in args.paths)
            ]
        elif args.known_only:
            result_envs = [e for k, e in envs_by_key.items() if k in discovered_keys]
        else:
            result_envs = list(envs_by_key.values())

        # Filter by --search if requested
        if args.search:
            target_name, target_ver = parse_search_query(args.search)
            matching = []
            for env in result_envs:
                if not env.packages:
                    packages.fill(env)
                if env_matches_search(env, target_name, target_ver):
                    matching.append(env)
            result_envs = matching

        # Sort envs deterministically
        result_envs.sort(key=lambda e: (e.name.lower(), e.path.lower()))

        # Update cache atomically (strictly inside places.cache_path, never modifying settings)
        try:
            cache_map = {e.key: e for e in cached}
            for e in result_envs:
                cache_map[e.key] = e
            store.save_cache(places.cache_path, list(cache_map.values()))
        except Exception:
            pass

        # Output results
        try:
            if args.json:
                scanned_at = datetime.datetime.now(datetime.timezone.utc)
                doc = build_json(result_envs, places, args, environ, scanned_at)
                stdout.write(json.dumps(doc, indent=2) + "\n")
                stdout.flush()
            else:
                table_text = format_table(result_envs, args)
                stdout.write(table_text + "\n")
                stdout.flush()
        except Exception as exc:
            _safe_write(stderr, f"output error: {exc}\n")
            return 3

        return 0

    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except BaseException as exc:
        _safe_write(stderr, f"error: {exc}\n")
        return 3


if __name__ == "__main__":
    sys.exit(main())
