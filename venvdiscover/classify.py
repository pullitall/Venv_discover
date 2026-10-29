"""Classify a folder as a conda base, conda env, venv, app-owned venv, temp venv, or python install."""

from __future__ import annotations

import os

from venvdiscover.model import Env, Kind, display_name, is_under, longpath, norm_key
from venvdiscover.places import Places


def _has_temp_component(path: str) -> bool:
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep) if p]
    norm_parts = [p.lower() for p in norm_key(path).split("\\") if p]
    return any(p in ("temp", "tmp") for p in parts) or any(p in ("temp", "tmp") for p in norm_parts)


def _first_component_below_home(path: str, home: str) -> str:
    """Return the first path component directly below home, or empty string."""
    try:
        rel = os.path.relpath(norm_key(path), norm_key(home))
    except ValueError:
        return ""
    if rel == "." or rel.startswith(".."):
        return ""
    parts = [p for p in rel.split("\\") if p and p != "."]
    return parts[0] if parts else ""


def probe(path: str, places: Places) -> Env | None:
    """Probe a folder and return an Env if recognised, else None.

    Fills path, kind, name and conda_root.
    """
    lp = longpath(path)
    conda_meta = os.path.join(lp, "conda-meta")

    # 1. Conda base: conda-meta/ exists and (condabin/ or envs/ exists)
    if os.path.isdir(conda_meta):
        if os.path.isdir(os.path.join(lp, "condabin")) or os.path.isdir(os.path.join(lp, "envs")):
            return Env(path=path, kind=Kind.CONDA_BASE, name=display_name(path), conda_root=path)

    # 2. Conda env: conda-meta/history or conda-meta/pixi exists, or at least one package record (*.json)
    if os.path.isdir(conda_meta):
        is_conda_env = False
        if os.path.isfile(os.path.join(conda_meta, "history")) or os.path.isfile(os.path.join(conda_meta, "pixi")):
            is_conda_env = True
        else:
            try:
                with os.scandir(conda_meta) as it:
                    for entry in it:
                        try:
                            if entry.is_file() and entry.name.lower().endswith(".json"):
                                is_conda_env = True
                                break
                        except OSError:
                            continue
            except OSError:
                pass

        if is_conda_env:
            parent = os.path.dirname(path.rstrip("\\/"))
            grandparent = os.path.dirname(parent)
            if os.path.basename(parent).lower() == "envs" and os.path.isdir(longpath(os.path.join(grandparent, "conda-meta"))):
                conda_root = grandparent
            else:
                conda_root = None
            return Env(path=path, kind=Kind.CONDA, name=display_name(path), conda_root=conda_root)

    # 3. Venv: pyvenv.cfg exists
    if os.path.isfile(os.path.join(lp, "pyvenv.cfg")):
        if is_under(path, places.temp) or _has_temp_component(path):
            kind = Kind.TEMP_VENV
        elif (
            is_under(path, places.appdata)
            or is_under(path, places.localappdata)
            or any(is_under(path, pf) for pf in places.program_files)
            or _first_component_below_home(path, places.home).startswith(".")
        ):
            kind = Kind.APP_VENV
        else:
            kind = Kind.VENV
        return Env(path=path, kind=kind, name=display_name(path))

    return None


def python_install(path: str) -> Env | None:
    """Return an Env with Kind.PYTHON if path is a Python installation, else None."""
    lp = longpath(path)
    if not os.path.isfile(os.path.join(lp, "python.exe")):
        return None
    if not os.path.isfile(os.path.join(lp, "Lib", "os.py")):
        return None
    if os.path.exists(os.path.join(lp, "pyvenv.cfg")) or os.path.exists(os.path.join(lp, "conda-meta")):
        return None
    return Env(path=path, kind=Kind.PYTHON, name=display_name(path))
