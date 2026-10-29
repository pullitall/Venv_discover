"""Core data types and path helpers shared by every module."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum


class Kind(str, Enum):
    CONDA_BASE = "conda base"
    CONDA = "conda"
    VENV = "venv"
    APP_VENV = "app-owned"
    TEMP_VENV = "temp"
    PYTHON = "python install"


LOCKED_KINDS = frozenset({Kind.CONDA_BASE, Kind.PYTHON})

# Folder names that say nothing on their own; the parent folder names the env instead.
GENERIC_NAMES = frozenset({".venv", "venv", "env", ".env", ".build-venv", "virtualenv"})

_LONG_PREFIX = "\\\\?\\"


@dataclass
class Package:
    name: str
    version: str
    source: str  # "pip" | "conda"


@dataclass
class Env:
    path: str
    kind: Kind
    name: str
    python_version: str = ""
    packages: list[Package] = field(default_factory=list)
    size: int | None = None  # bytes; None = not measured yet
    last_used: float | None = None  # epoch seconds
    conda_root: str | None = None
    stamp: float = 0.0  # package_stamp() when the packages were read
    key: str = ""  # norm_key(path), filled in automatically

    def __post_init__(self) -> None:
        if not self.key:
            self.key = norm_key(self.path)

    @property
    def locked(self) -> bool:
        return self.kind in LOCKED_KINDS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["packages"] = [[p.name, p.version, p.source] for p in self.packages]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Env:
        d = dict(d)
        d["kind"] = Kind(d["kind"])
        d["packages"] = [Package(*p) for p in d.get("packages", [])]
        return cls(**d)


def _strip_long_prefix(path: str) -> str:
    if path.startswith(_LONG_PREFIX + "UNC\\"):
        return "\\\\" + path[len(_LONG_PREFIX) + 4:]
    if path.startswith(_LONG_PREFIX):
        return path[len(_LONG_PREFIX):]
    return path


def norm_key(path: str) -> str:
    """Identity of a folder: real path (junctions resolved), case-folded, no \\\\?\\ prefix."""
    return os.path.normcase(_strip_long_prefix(os.path.realpath(path)))


def is_under(path: str, parent: str) -> bool:
    """True if path is parent itself or anywhere inside it."""
    p, q = norm_key(path), norm_key(parent)
    return p == q or p.startswith(q.rstrip("\\") + "\\")


def display_name(path: str) -> str:
    path = path.rstrip("\\/")
    base = os.path.basename(path)
    if base.lower() in GENERIC_NAMES:
        parent = os.path.basename(os.path.dirname(path))
        if parent:
            return f"{parent}\\{base}"
    return base or path


def longpath(path: str) -> str:
    """Absolute path with the \\\\?\\ prefix once it gets near MAX_PATH; short paths unchanged."""
    if path.startswith(_LONG_PREFIX) or len(path) < 240:
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\"):
        return _LONG_PREFIX + "UNC\\" + p[2:]
    return _LONG_PREFIX + p


def normalize_name(name: str) -> str:
    """PEP 503 package name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()
