"""Search, filter, and sorting queries across environments and packages."""

from __future__ import annotations

from collections.abc import Iterable
import re

from venvdiscover.model import Env, Kind, Package, normalize_name


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for numerical sorting. Empty string gives ()."""
    if not v:
        return ()
    parts: list[int] = []
    for part in re.split(r"[^\d]+", v):
        if part:
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
    return tuple(parts)


def search(envs: Iterable[Env], q: str) -> list[tuple[Env, Package]]:
    """Search across envs for packages whose normalized name contains normalized q."""
    norm_q = normalize_name(q.strip())
    if not norm_q:
        return []

    results: list[tuple[Env, Package]] = []
    for env in envs:
        for pkg in env.packages:
            if norm_q in normalize_name(pkg.name):
                results.append((env, pkg))

    results.sort(key=lambda item: (item[1].name.lower(), item[0].name.lower(), item[0].path.lower()))
    return results


def filter_packages(pkgs: list[Package], q: str) -> list[Package]:
    """Filter package list by substring of normalized package name."""
    norm_q = normalize_name(q.strip())
    if not norm_q:
        return list(pkgs)
    return [p for p in pkgs if norm_q in normalize_name(p.name)]


def env_sort_key(env: Env, column: str) -> tuple:
    """Return a sort key tuple for an environment by column. TEMP_VENV always sorts last."""
    is_temp = 1 if env.kind == Kind.TEMP_VENV else 0

    if column == "name":
        return (is_temp, env.name.lower(), env.path.lower())
    if column == "kind":
        return (is_temp, env.kind.value.lower(), env.name.lower())
    if column == "python":
        return (is_temp, _parse_version_tuple(env.python_version), env.name.lower())
    if column == "packages":
        return (is_temp, len(env.packages), env.name.lower())
    if column == "size":
        size_part = (0, 0) if env.size is None else (1, env.size)
        return (is_temp, size_part, env.name.lower())
    if column == "last_used":
        last_part = (0, 0.0) if env.last_used is None else (1, env.last_used)
        return (is_temp, last_part, env.name.lower())
    if column == "location":
        return (is_temp, env.path.lower(), env.name.lower())

    return (is_temp, env.name.lower(), env.path.lower())
