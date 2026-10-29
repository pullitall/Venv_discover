"""Persistence for environments cache, application settings, and action log."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass

from venvdiscover.model import Env, longpath


@dataclass
class Settings:
    drives: list[str] | None = None  # None = default_scan_drives(local_drives())
    terminal: str = "cmd"
    theme: str = "dark"
    palette: str = "default"


def load_cache(path: str) -> list[Env]:
    """Load cached environments from JSON file. Return [] if missing, unreadable, or wrong version."""
    try:
        with open(longpath(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        if data.get("version") != 1:
            return []
        raw_envs = data.get("envs")
        if not isinstance(raw_envs, list):
            return []
        envs = []
        for d in raw_envs:
            if not isinstance(d, dict):
                return []
            envs.append(Env.from_dict(d))
        return envs
    except Exception:
        return []


def save_cache(path: str, envs: Iterable[Env]) -> None:
    """Save environments to path as {"version": 1, "envs": [...]}. Atomic write via .tmp then os.replace."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + ".tmp"
    data = {
        "version": 1,
        "envs": [e.to_dict() for e in envs],
    }
    with open(longpath(tmp_path), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    os.replace(longpath(tmp_path), longpath(path))


def load_settings(path: str) -> Settings:
    """Load settings from path. Return default Settings() if missing, unreadable, or invalid."""
    try:
        with open(longpath(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            drives = data.get("drives")
            terminal = data.get("terminal")
            term_str = str(terminal) if isinstance(terminal, str) and terminal else "cmd"
            theme = data.get("theme")
            theme_str = str(theme) if isinstance(theme, str) and theme else "dark"
            palette = data.get("palette")
            palette_str = str(palette) if isinstance(palette, str) and palette else "default"
            return Settings(
                drives=drives if isinstance(drives, list) else None,
                terminal=term_str,
                theme=theme_str,
                palette=palette_str,
            )
    except Exception:
        pass
    return Settings()


def save_settings(path: str, s: Settings) -> None:
    """Save settings to path atomically via .tmp then os.replace."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + ".tmp"
    data = {
        "drives": s.drives,
        "terminal": s.terminal if s.terminal else "cmd",
        "theme": s.theme if s.theme else "dark",
        "palette": s.palette if s.palette else "default",
    }
    with open(longpath(tmp_path), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    os.replace(longpath(tmp_path), longpath(path))


def append_log(path: str, action: str, env_path: str, detail: str = "") -> None:
    """Append a single JSON line to log at path with ts, action, env_path, and detail."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    entry = {
        "ts": time.time(),
        "action": action,
        "env_path": env_path,
        "detail": detail,
    }
    with open(longpath(path), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
