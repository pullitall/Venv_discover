"""Read installed packages and Python version directly from metadata files."""

from __future__ import annotations

import os
import re

from venvdiscover.model import Env, Kind, Package, longpath, normalize_name


def _parse_metadata_file(filepath: str) -> tuple[str, str]:
    name = ""
    version = ""
    try:
        with open(longpath(filepath), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    break
                if ":" not in line:
                    continue
                key, val = line.split(":", 1)
                k = key.strip().lower()
                if k == "name" and not name:
                    name = val.strip()
                elif k == "version" and not version:
                    version = val.strip()
    except OSError:
        pass
    return name, version


def _split_stem(stem: str) -> tuple[str, str]:
    if "-" in stem:
        name, version = stem.rsplit("-", 1)
    else:
        name, version = stem, ""
    return name, version


def _read_installer(filepath: str) -> str:
    try:
        with open(longpath(filepath), "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip().lower()
    except OSError:
        return ""


def read_packages(env: Env) -> list[Package]:
    """Read installed packages for an environment, sorted case-insensitively by name."""
    site_packages = os.path.join(env.path, "Lib", "site-packages")
    sp_entries: list[tuple[str, str, str]] = []

    if os.path.isdir(longpath(site_packages)):
        try:
            with os.scandir(longpath(site_packages)) as it:
                for entry in it:
                    try:
                        if entry.name.endswith(".dist-info"):
                            stem = entry.name[:-10]
                            meta_file = os.path.join(entry.path, "METADATA")
                            name, version = _parse_metadata_file(meta_file)
                            if not name or not version:
                                f_name, f_ver = _split_stem(stem)
                                if not name:
                                    name = f_name
                                if not version:
                                    version = f_ver
                            if name:
                                installer_file = os.path.join(entry.path, "INSTALLER")
                                installer = _read_installer(installer_file)
                                sp_entries.append((name, version, installer))
                        elif entry.name.endswith(".egg-info"):
                            stem = entry.name[:-9]
                            if entry.is_dir():
                                pkg_info = os.path.join(entry.path, "PKG-INFO")
                            else:
                                pkg_info = entry.path
                            name, version = _parse_metadata_file(pkg_info)
                            if not name or not version:
                                f_name, f_ver = _split_stem(stem)
                                if not name:
                                    name = f_name
                                if not version:
                                    version = f_ver
                            if name:
                                sp_entries.append((name, version, "pip"))
                    except OSError:
                        continue
        except OSError:
            pass

    if env.kind in (Kind.CONDA, Kind.CONDA_BASE):
        conda_pkgs: list[Package] = []
        conda_norm_names: set[str] = set()
        meta_dir = os.path.join(env.path, "conda-meta")
        if os.path.isdir(longpath(meta_dir)):
            try:
                with os.scandir(longpath(meta_dir)) as it:
                    for entry in it:
                        try:
                            if entry.name.endswith(".json"):
                                stem = entry.name[:-5]
                                parts = stem.rsplit("-", 2)
                                if len(parts) == 3:
                                    name, version, _ = parts
                                    conda_pkgs.append(Package(name=name, version=version, source="conda"))
                                    conda_norm_names.add(normalize_name(name))
                        except OSError:
                            continue
            except OSError:
                pass

        for name, version, installer in sp_entries:
            if normalize_name(name) in conda_norm_names:
                continue
            if installer == "conda":
                continue
            conda_pkgs.append(Package(name=name, version=version, source="pip"))

        return sorted(conda_pkgs, key=lambda p: p.name.lower())

    pkgs = [
        Package(name=name, version=version, source="pip")
        for name, version, _ in sp_entries
    ]
    return sorted(pkgs, key=lambda p: p.name.lower())


def python_version(env: Env) -> str:
    """Return the Python version (e.g. '3.12.4' or '3.13') for the environment."""
    if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
        cfg_path = os.path.join(env.path, "pyvenv.cfg")
        try:
            with open(longpath(cfg_path), "r", encoding="utf-8", errors="replace") as f:
                ver_val = ""
                for line in f:
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip().lower()
                        if k == "version":
                            ver_val = v.strip()
                            break
                        elif k == "version_info" and not ver_val:
                            ver_val = v.strip()
                if ver_val:
                    m = re.match(r"^(\d+\.\d+(?:\.\d+)?)", ver_val)
                    if m:
                        return m.group(1)
        except OSError:
            pass
        return ""

    if env.kind in (Kind.CONDA, Kind.CONDA_BASE):
        meta_dir = os.path.join(env.path, "conda-meta")
        if os.path.isdir(longpath(meta_dir)):
            try:
                with os.scandir(longpath(meta_dir)) as it:
                    for entry in it:
                        try:
                            if entry.name.startswith("python-") and entry.name.endswith(".json"):
                                stem = entry.name[:-5]
                                parts = stem.rsplit("-", 2)
                                if len(parts) == 3 and parts[0] == "python":
                                    return parts[1]
                        except OSError:
                            continue
            except OSError:
                pass
        return ""

    if env.kind == Kind.PYTHON:
        try:
            with os.scandir(longpath(env.path)) as it:
                for entry in it:
                    try:
                        m = re.match(r"^python3(\d+)\.dll$", entry.name, re.IGNORECASE)
                        if m:
                            return f"3.{m.group(1)}"
                    except OSError:
                        continue
        except OSError:
            pass
        return ""

    return ""


def package_stamp(env: Env) -> float:
    """Return the max mtime of Lib\\site-packages and conda-meta, or 0.0."""
    times: list[float] = []
    sp = os.path.join(env.path, "Lib", "site-packages")
    try:
        times.append(os.path.getmtime(longpath(sp)))
    except OSError:
        pass
    meta = os.path.join(env.path, "conda-meta")
    try:
        times.append(os.path.getmtime(longpath(meta)))
    except OSError:
        pass
    return max(times) if times else 0.0


def fill(env: Env) -> Env:
    """Populate packages, python_version, stamp, and last_used on the environment."""
    from venvdiscover import sizes

    env.packages = read_packages(env)
    env.python_version = python_version(env)
    env.stamp = package_stamp(env)
    env.last_used = sizes.last_used(env)
    return env
