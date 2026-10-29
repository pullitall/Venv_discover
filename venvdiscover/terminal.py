"""Terminal detection, start directory, activation commands, and process launching."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
import re
import shutil
import subprocess

from venvdiscover.known import find_conda_roots, registry_install_paths
from venvdiscover.model import Env, Kind, is_under, norm_key
from venvdiscover.places import Places


@dataclass(frozen=True)
class Terminal:
    id: str
    label: str
    exe: str


def _env_get(environ: Mapping[str, str], key: str, default: str = "") -> str:
    key_lower = key.lower()
    for k, v in environ.items():
        if k.lower() == key_lower:
            return v
    return default


def available_terminals(environ: Mapping[str, str] | None = None) -> list[Terminal]:
    """Find installed terminals on the system without touching real os.environ when provided."""
    if environ is None:
        environ = os.environ

    path_val = _env_get(environ, "PATH")
    sys_root = _env_get(environ, "SystemRoot", r"C:\Windows")
    prog_files = _env_get(environ, "ProgramFiles", r"C:\Program Files")
    local_app = _env_get(environ, "LOCALAPPDATA")

    result: list[Terminal] = []

    # 1. cmd - always listed
    cmd_exe = _env_get(environ, "COMSPEC")
    if not cmd_exe or not os.path.isfile(cmd_exe):
        which_cmd = shutil.which("cmd.exe", path=path_val)
        if which_cmd and os.path.isfile(which_cmd):
            cmd_exe = which_cmd
        else:
            cand = os.path.join(sys_root, "System32", "cmd.exe")
            cmd_exe = cand if os.path.isfile(cand) else "cmd.exe"
    result.append(Terminal(id="cmd", label="Command Prompt", exe=cmd_exe))

    # 2. powershell - always listed
    ps_cand = os.path.join(sys_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(ps_cand):
        ps_exe = ps_cand
    else:
        which_ps = shutil.which("powershell.exe", path=path_val)
        ps_exe = which_ps if (which_ps and os.path.isfile(which_ps)) else "powershell.exe"
    result.append(Terminal(id="powershell", label="Windows PowerShell", exe=ps_exe))

    # 3. pwsh (PowerShell 7) - on PATH or under %ProgramFiles%\PowerShell\7\
    which_pwsh = shutil.which("pwsh.exe", path=path_val)
    pwsh_pf = os.path.join(prog_files, "PowerShell", "7", "pwsh.exe")
    if which_pwsh and os.path.isfile(which_pwsh):
        result.append(Terminal(id="pwsh", label="PowerShell 7", exe=which_pwsh))
    elif os.path.isfile(pwsh_pf):
        result.append(Terminal(id="pwsh", label="PowerShell 7", exe=pwsh_pf))

    # 4. Windows Terminal (wt-powershell and wt-cmd) - wt.exe on PATH or in %LOCALAPPDATA%\Microsoft\WindowsApps\
    which_wt = shutil.which("wt.exe", path=path_val)
    wt_cand = os.path.join(local_app, "Microsoft", "WindowsApps", "wt.exe") if local_app else ""
    wt_exe: str | None = None
    if which_wt and os.path.isfile(which_wt):
        wt_exe = which_wt
    elif wt_cand and os.path.isfile(wt_cand):
        wt_exe = wt_cand

    if wt_exe:
        result.append(Terminal(id="wt-powershell", label="Windows Terminal (PowerShell)", exe=wt_exe))
        result.append(Terminal(id="wt-cmd", label="Windows Terminal (Command Prompt)", exe=wt_exe))

    # 5. Git Bash - %ProgramFiles%\Git\bin\bash.exe exists
    git_cand = os.path.join(prog_files, "Git", "bin", "bash.exe")
    if os.path.isfile(git_cand):
        result.append(Terminal(id="gitbash", label="Git Bash", exe=git_cand))

    return result


def start_dir(env: Env) -> str:
    """Return the start directory for an environment's terminal."""
    project_file = os.path.join(env.path, ".project")
    if os.path.isfile(project_file):
        try:
            with open(project_file, "r", encoding="utf-8", errors="replace") as f:
                target = f.read().strip()
            if target and os.path.isdir(target):
                return target
        except OSError:
            pass
    return env.path


def _resolve_conda_root_and_target(
    env: Env,
    places: Places,
    environ: Mapping[str, str],
    registry_paths: list[str] | None = None,
) -> tuple[str | None, str]:
    if env.kind == Kind.CONDA_BASE:
        return env.path, "base"

    root = env.conda_root
    if not root:
        parent = os.path.dirname(os.path.normpath(env.path))
        if os.path.basename(parent).lower() == "envs":
            grandparent = os.path.dirname(parent)
            if os.path.isdir(os.path.join(grandparent, "conda-meta")):
                root = grandparent
    if not root:
        reg = registry_paths if registry_paths is not None else registry_install_paths()
        roots = find_conda_roots(places, environ, registry_paths=reg)
        if roots:
            root = roots[0]

    if not root:
        return None, env.path

    target = env.path
    if env.conda_root:
        expected_parent = os.path.join(env.conda_root, "envs")
        if norm_key(os.path.dirname(os.path.normpath(env.path))) == norm_key(expected_parent):
            target = os.path.basename(env.path.rstrip("\\/"))

    return root, target


def _format_wt_dir(path: str) -> str:
    """Format path for Windows Terminal -d flag, avoiding trailing backslash escaping the quote.

    Strip trailing backslashes, except on a drive root (e.g. 'C:\\'), where the backslash is doubled ('C:\\\\').
    """
    clean = path.rstrip("/\\")
    if not clean:
        return path
    drive, rest = os.path.splitdrive(clean)
    if drive and not rest:
        return drive + "\\\\"
    return clean


def _bash_escape(s: str) -> str:
    """Escape a string for use inside single quotes in bash: foo'bar -> foo'\\''bar."""
    return s.replace("'", r"'\''")


def _gitbash_rcfile_path(env: Env, places: Places) -> str:
    terminals_dir = os.path.join(places.app_dir, "terminals")
    os.makedirs(terminals_dir, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]+", "_", env.name)[:30]
    key_hash = hashlib.sha256(norm_key(env.path).encode("utf-8")).hexdigest()[:12]
    return os.path.join(terminals_dir, f"{safe_name}_{key_hash}.bashrc")


def _write_gitbash_rcfile(
    env: Env,
    places: Places,
    environ: Mapping[str, str],
    registry_paths: list[str] | None = None,
) -> str:
    rc_path = _gitbash_rcfile_path(env, places)
    lines: list[str] = [
        "if [ -f ~/.bashrc ]; then",
        "    . ~/.bashrc",
        "fi",
    ]

    if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
        act_posix = _bash_escape(os.path.join(env.path, "Scripts", "activate").replace("\\", "/"))
        lines.append(f". '{act_posix}'")
    elif env.kind in (Kind.CONDA, Kind.CONDA_BASE):
        root, target = _resolve_conda_root_and_target(env, places, environ, registry_paths=registry_paths)
        if root:
            root_posix = _bash_escape(root.replace("\\", "/"))
            target_posix = _bash_escape(target.replace("\\", "/"))
            lines.append(f"eval \"$('{root_posix}/Scripts/conda.exe' shell.bash hook)\"")
            lines.append(f"conda activate '{target_posix}'")

    with open(rc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    return rc_path


def terminal_command(
    env: Env,
    terminal: Terminal,
    places: Places,
    environ: Mapping[str, str] | None = None,
    registry_paths: list[str] | None = None,
) -> tuple[list[str] | str, str, dict[str, str] | None]:
    """Compute the command (argv list or command string), cwd, and child environment."""
    if environ is None:
        environ = os.environ

    cwd = start_dir(env)
    child_env: dict[str, str] | None = None

    conda_root: str | None = None
    conda_target: str = env.path
    if env.kind in (Kind.CONDA, Kind.CONDA_BASE):
        conda_root, conda_target = _resolve_conda_root_and_target(env, places, environ, registry_paths=registry_paths)

    if env.kind == Kind.PYTHON:
        child_env = dict(environ)
        cur_path = child_env.get("PATH", "")
        install_scripts = os.path.join(env.path, "Scripts")
        prefix = f"{env.path};{install_scripts}"
        child_env["PATH"] = f"{prefix};{cur_path}" if cur_path else prefix
    elif env.kind in (Kind.CONDA, Kind.CONDA_BASE) and conda_root is None:
        child_env = dict(environ)
        cur_path = child_env.get("PATH", "")
        lib_bin = os.path.join(env.path, "Library", "bin")
        scripts = os.path.join(env.path, "Scripts")
        prefix = f"{env.path};{lib_bin};{scripts}"
        child_env["PATH"] = f"{prefix};{cur_path}" if cur_path else prefix

    cmd: list[str] | str

    # 1. cmd - returns command string so inner quotes are not mangled by list2cmdline
    if terminal.id == "cmd":
        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
            act_bat = os.path.join(env.path, "Scripts", "activate.bat")
            cmd = f'"{terminal.exe}" /k call "{act_bat}"'
        elif env.kind in (Kind.CONDA, Kind.CONDA_BASE):
            if conda_root:
                conda_bat = os.path.join(conda_root, "condabin", "conda.bat")
                cmd = f'"{terminal.exe}" /k call "{conda_bat}" activate "{conda_target}"'
            else:
                cmd = f'"{terminal.exe}"'
        else:  # Kind.PYTHON
            cmd = f'"{terminal.exe}"'

    # 2. wt-cmd - returns command string
    elif terminal.id == "wt-cmd":
        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
            act_bat = os.path.join(env.path, "Scripts", "activate.bat")
            inner_cmd = f'cmd.exe /k call "{act_bat}"'
        elif env.kind in (Kind.CONDA, Kind.CONDA_BASE):
            if conda_root:
                conda_bat = os.path.join(conda_root, "condabin", "conda.bat")
                inner_cmd = f'cmd.exe /k call "{conda_bat}" activate "{conda_target}"'
            else:
                inner_cmd = "cmd.exe"
        else:  # Kind.PYTHON
            inner_cmd = "cmd.exe"

        escaped_inner = inner_cmd.replace(";", r"\;")
        wt_cwd = _format_wt_dir(cwd).replace(";", r"\;")
        cmd = f'"{terminal.exe}" -d "{wt_cwd}" {escaped_inner}'

    # 3. powershell and pwsh - return list
    elif terminal.id in ("powershell", "pwsh"):
        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
            act_ps1 = os.path.join(env.path, "Scripts", "Activate.ps1").replace("'", "''")
            cmd = [terminal.exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f"& '{act_ps1}'"]
        elif env.kind in (Kind.CONDA, Kind.CONDA_BASE):
            if conda_root:
                hook_ps1 = os.path.join(conda_root, "shell", "condabin", "conda-hook.ps1").replace("'", "''")
                target_ps = conda_target.replace("'", "''")
                cmd = [
                    terminal.exe,
                    "-NoExit",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"& '{hook_ps1}'; conda activate '{target_ps}'",
                ]
            else:
                cmd = [terminal.exe, "-NoExit"]
        else:  # Kind.PYTHON
            cmd = [terminal.exe, "-NoExit"]

    # 4. wt-powershell - returns list
    elif terminal.id == "wt-powershell":
        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
            act_ps1 = os.path.join(env.path, "Scripts", "Activate.ps1").replace("'", "''")
            inner_ps = ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f"& '{act_ps1}'"]
        elif env.kind in (Kind.CONDA, Kind.CONDA_BASE):
            if conda_root:
                hook_ps1 = os.path.join(conda_root, "shell", "condabin", "conda-hook.ps1").replace("'", "''")
                target_ps = conda_target.replace("'", "''")
                inner_ps = [
                    "powershell.exe",
                    "-NoExit",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"& '{hook_ps1}'; conda activate '{target_ps}'",
                ]
            else:
                inner_ps = ["powershell.exe", "-NoExit"]
        else:  # Kind.PYTHON
            inner_ps = ["powershell.exe", "-NoExit"]

        escaped_inner = [arg.replace(";", r"\;") for arg in inner_ps]
        wt_cwd = _format_wt_dir(cwd).replace(";", r"\;")
        cmd = [terminal.exe, "-d", wt_cwd] + escaped_inner

    # 5. gitbash - returns list
    elif terminal.id == "gitbash":
        rc_path = _write_gitbash_rcfile(env, places, environ, registry_paths=registry_paths)
        cmd = [terminal.exe, "--rcfile", rc_path, "-i"]

    else:
        cmd = [terminal.exe]

    return cmd, cwd, child_env


def open_terminal(
    env: Env,
    terminal: Terminal,
    places: Places,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Launch terminal process for the environment in a new console window."""
    cmd, cwd, child_env = terminal_command(env, terminal, places, environ=environ)
    flags = 0
    if not terminal.id.startswith("wt"):
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010) if os.name == "nt" else 0

    subprocess.Popen(
        cmd,
        cwd=cwd,
        env=child_env,
        creationflags=flags,
    )
