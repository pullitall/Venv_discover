"""Where things live on this PC. Injected everywhere so tests can point it at a temp tree."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Places:
    home: str
    appdata: str
    localappdata: str
    temp: str
    programdata: str
    program_files: tuple[str, ...] = ()

    @property
    def app_dir(self) -> str:
        return os.path.join(self.localappdata, "VenvDiscover")

    @property
    def cache_path(self) -> str:
        return os.path.join(self.app_dir, "cache.json")

    @property
    def settings_path(self) -> str:
        return os.path.join(self.app_dir, "settings.json")

    @property
    def log_path(self) -> str:
        return os.path.join(self.app_dir, "actions.log")

    @property
    def environments_txt(self) -> str:
        return os.path.join(self.home, ".conda", "environments.txt")

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Places:
        home = environ.get("USERPROFILE") or os.path.expanduser("~")
        appdata = environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        local = environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        temp = environ.get("TEMP") or environ.get("TMP") or os.path.join(local, "Temp")
        programdata = environ.get("ProgramData") or environ.get("PROGRAMDATA") or r"C:\ProgramData"
        program_files = tuple(dict.fromkeys(
            v for k in ("ProgramFiles", "ProgramFiles(x86)") if (v := environ.get(k))
        ))
        return cls(home, appdata, local, temp, programdata, program_files)
