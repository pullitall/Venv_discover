"""Actions on environments: activate command, open folder, conda clean."""

from __future__ import annotations

from collections.abc import Callable
import os
import subprocess

from venvdiscover.model import Env, Kind, longpath, norm_key


def activate_command(env: Env) -> str:
    """Return the terminal command to activate the environment, or empty string."""
    if env.kind == Kind.CONDA_BASE:
        return "conda activate base"

    if env.kind == Kind.CONDA:
        if env.conda_root:
            expected_parent = os.path.join(env.conda_root, "envs")
            if norm_key(os.path.dirname(os.path.normpath(env.path))) == norm_key(expected_parent):
                base_name = os.path.basename(env.path.rstrip("\\/"))
                return f"conda activate {base_name}"
        return f'conda activate "{env.path}"'

    if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
        act_script = os.path.join(env.path.rstrip("\\/"), "Scripts", "activate")
        return f'"{act_script}"'

    return ""


def open_folder(path: str) -> None:
    """Open folder in Windows Explorer."""
    if hasattr(os, "startfile"):
        os.startfile(path)
    else:
        subprocess.run(["explorer", path])


def conda_exe(conda_root: str) -> str | None:
    """Find conda executable under a conda root (Scripts first, then condabin fallback)."""
    scripts_exe = os.path.join(conda_root, "Scripts", "conda.exe")
    if os.path.isfile(longpath(scripts_exe)):
        return scripts_exe

    condabin_bat = os.path.join(conda_root, "condabin", "conda.bat")
    if os.path.isfile(longpath(condabin_bat)):
        return condabin_bat

    return None


def run_conda_clean(exe: str, on_line: Callable[[str], None]) -> int:
    """Run conda clean --all -y via subprocess, merging stdout/stderr and calling on_line per line."""
    cmd = [exe, "clean", "--all", "-y"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    proc_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
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
