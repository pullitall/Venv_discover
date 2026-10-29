"""Fake env folders for tests. Never point these at the owner's real folders.

Everything goes under <repo>\\.test-tmp, not %TEMP%: a "Temp" folder anywhere in the
path makes classify() call every venv a temp venv.
"""

from __future__ import annotations

import json
import os
import tempfile

from venvdiscover.model import longpath
from venvdiscover.places import Places

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TMP = os.path.join(REPO_ROOT, ".test-tmp")

if any(part.lower() in ("temp", "tmp") for part in TEST_TMP.split(os.sep)):
    raise RuntimeError(f"tests can't run from {REPO_ROOT}: a 'temp'/'tmp' folder in the path "
                       "breaks env classification. Move the checkout.")


def tmpdir() -> tempfile.TemporaryDirectory:
    os.makedirs(TEST_TMP, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TEST_TMP)


def write_file(path: str, data: str | bytes = "") -> str:
    lp = longpath(path)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    if isinstance(data, bytes):
        with open(lp, "wb") as f:
            f.write(data)
    else:
        with open(lp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
    return path


def _mkdir(path: str) -> str:
    os.makedirs(longpath(path), exist_ok=True)
    return path


def _dist_info(site_packages: str, name: str, version: str, installer: str) -> None:
    d = os.path.join(site_packages, f"{name.replace('-', '_')}-{version}.dist-info")
    write_file(os.path.join(d, "METADATA"),
               f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nbody\n")
    write_file(os.path.join(d, "INSTALLER"), f"{installer}\n")


def _conda_meta(env: str, packages: dict[str, str]) -> None:
    meta = os.path.join(env, "conda-meta")
    write_file(os.path.join(meta, "history"), "==> 2026-10-26 12:00:00 <==\n")
    for name, version in packages.items():
        write_file(os.path.join(meta, f"{name}-{version}-h0_0.json"),
                   json.dumps({"name": name, "version": version, "build": "h0_0"}))


def make_venv(parent: str, name: str = ".venv", version: str = "3.12.4",
              packages: dict[str, str] | None = None, cfg_key: str = "version") -> str:
    env = os.path.join(parent, name)
    write_file(os.path.join(env, "pyvenv.cfg"),
               f"home = C:\\Python312\ninclude-system-site-packages = false\n{cfg_key} = {version}\n")
    write_file(os.path.join(env, "Scripts", "python.exe"))
    sp = _mkdir(os.path.join(env, "Lib", "site-packages"))
    for n, v in (packages or {}).items():
        _dist_info(sp, n, v, "pip")
    return env


def make_conda_root(parent: str, name: str = "miniforge3", python: str = "3.12.4",
                    packages: dict[str, str] | None = None) -> str:
    root = os.path.join(parent, name)
    _conda_meta(root, {"python": python, **(packages or {})})
    _mkdir(os.path.join(root, "condabin"))
    _mkdir(os.path.join(root, "envs"))
    write_file(os.path.join(root, "python.exe"))
    sp = _mkdir(os.path.join(root, "Lib", "site-packages"))
    for n, v in (packages or {}).items():
        _dist_info(sp, n, v, "conda")
    return root


def make_conda_env(root: str, name: str, python: str = "3.11.9",
                   conda_packages: dict[str, str] | None = None,
                   pip_packages: dict[str, str] | None = None) -> str:
    env = os.path.join(root, "envs", name)
    _conda_meta(env, {"python": python, **(conda_packages or {})})
    write_file(os.path.join(env, "python.exe"))
    sp = _mkdir(os.path.join(env, "Lib", "site-packages"))
    for n, v in (conda_packages or {}).items():
        _dist_info(sp, n, v, "conda")
    for n, v in (pip_packages or {}).items():
        _dist_info(sp, n, v, "pip")
    return env


def make_python_install(parent: str, name: str = "Python312", version: str = "3.12.4",
                        packages: dict[str, str] | None = None) -> str:
    d = os.path.join(parent, name)
    major, minor = version.split(".")[:2]
    write_file(os.path.join(d, "python.exe"))
    write_file(os.path.join(d, f"python{major}{minor}.dll"))
    write_file(os.path.join(d, "Lib", "os.py"), "# fake\n")
    sp = _mkdir(os.path.join(d, "Lib", "site-packages"))
    for n, v in (packages or {}).items():
        _dist_info(sp, n, v, "pip")
    return d


def fake_places(tmp: str) -> Places:
    home = os.path.join(tmp, "home")
    local = os.path.join(home, "AppData", "Local")
    places = Places(
        home=home,
        appdata=os.path.join(home, "AppData", "Roaming"),
        localappdata=local,
        temp=os.path.join(local, "Temp"),
        programdata=os.path.join(tmp, "ProgramData"),
        program_files=(os.path.join(tmp, "Program Files"),),
    )
    for d in (places.home, places.appdata, places.localappdata, places.temp,
              places.programdata, *places.program_files):
        _mkdir(d)
    return places
