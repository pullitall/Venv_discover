"""Discover conda roots, candidate environment folders, and Python installations."""

from __future__ import annotations

import os
import shutil
import tomllib
import winreg
from collections.abc import Mapping

from venvdiscover.classify import probe, python_install
from venvdiscover.connections import _read_env_lines
from venvdiscover.model import Kind, longpath, norm_key
from venvdiscover.places import Places


def _conda_root_from_exe(path: str) -> str | None:
    if not path:
        return None
    try:
        p = os.path.abspath(path)
        folder = os.path.dirname(p)
        if os.path.basename(folder).lower() in ("scripts", "condabin", "bin"):
            return os.path.dirname(folder)
        return folder
    except Exception:
        return None


def find_conda_roots(
    places: Places,
    environ: Mapping[str, str],
    registry_paths: list[str] | None = None,
) -> list[str]:
    """Find conda base installations, deduplicated by norm_key."""
    candidates: list[str] = []

    # 1. CONDA_EXE environment variable
    conda_exe = environ.get("CONDA_EXE")
    if conda_exe:
        r = _conda_root_from_exe(conda_exe)
        if r:
            candidates.append(r)

    # 2. shutil.which("conda", path=environ.get("PATH", ""))
    path_val = environ.get("PATH", "")
    which_conda = shutil.which("conda", path=path_val)
    if which_conda:
        r = _conda_root_from_exe(which_conda)
        if r:
            candidates.append(r)

    # 3. environments.txt lines: either the line itself is a root, or grandparent of envs\x
    res = _read_env_lines(places.environments_txt)
    if res is not None:
        lines, _ = res
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            candidates.append(line)
            parent = os.path.dirname(line)
            if os.path.basename(parent).lower() == "envs":
                grandparent = os.path.dirname(parent)
                candidates.append(grandparent)

    # 4. Children of home, localappdata, programdata containing conda, forge or mamba
    search_dirs = (places.home, places.localappdata, places.programdata)
    keywords = ("conda", "forge", "mamba")
    for parent_dir in search_dirs:
        try:
            if os.path.isdir(longpath(parent_dir)):
                with os.scandir(longpath(parent_dir)) as it:
                    for entry in it:
                        try:
                            name_lower = entry.name.lower()
                            if any(k in name_lower for k in keywords):
                                if entry.is_dir():
                                    meta = os.path.join(entry.path, "conda-meta")
                                    if os.path.isdir(longpath(meta)):
                                        candidates.append(entry.path)
                        except OSError:
                            continue
        except OSError:
            pass

    # 5. Conda installs registered in Windows registry
    reg_paths = registry_paths if registry_paths is not None else []
    candidates.extend(reg_paths)

    # Filter: kept only if probe() says CONDA_BASE; deduplicated by norm_key
    seen: set[str] = set()
    roots: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        try:
            k = norm_key(cand)
            if k in seen:
                continue
            env = probe(cand, places)
            if env is not None and env.kind == Kind.CONDA_BASE:
                seen.add(k)
                roots.append(cand)
        except OSError:
            continue

    return roots


def _find_conda_config_files(
    places: Places,
    environ: Mapping[str, str],
    conda_roots: list[str],
) -> list[str]:
    """Find all .condarc and condarc.d/*.yml files per Conda docs ('Searching for .condarc') and ~/.mambarc."""
    files: list[str] = []
    seen: set[str] = set()

    def _add_file(fp: str) -> None:
        try:
            if os.path.isfile(longpath(fp)):
                k = norm_key(fp)
                if k not in seen:
                    seen.add(k)
                    files.append(fp)
        except OSError:
            pass

    def _add_config_locations(base_dir: str) -> None:
        if not base_dir:
            return
        try:
            if not os.path.isdir(longpath(base_dir)):
                return
            for name in (".condarc", "condarc"):
                _add_file(os.path.join(base_dir, name))
            condarc_d = os.path.join(base_dir, "condarc.d")
            if os.path.isdir(longpath(condarc_d)):
                with os.scandir(longpath(condarc_d)) as it:
                    for entry in it:
                        try:
                            if entry.is_file() and entry.name.lower().endswith((".yml", ".yaml")):
                                _add_file(entry.path)
                        except OSError:
                            continue
        except OSError:
            pass

    # 1. %CONDARC% environment variable override
    condarc_env = environ.get("CONDARC")
    if condarc_env:
        _add_file(os.path.expandvars(os.path.expanduser(condarc_env)))

    # 2. System-wide: C:\ProgramData\conda\.condarc, condarc, condarc.d\*.yml
    _add_config_locations(os.path.join(places.programdata, "conda"))

    # 3. Each conda root: <root>\.condarc, condarc, condarc.d\*.yml
    for r in conda_roots:
        _add_config_locations(r)

    # 4. User-level: ~/.config/conda/..., ~/.conda/..., and ~/.condarc
    _add_config_locations(os.path.join(places.home, ".config", "conda"))
    _add_config_locations(os.path.join(places.home, ".conda"))
    _add_file(os.path.join(places.home, ".condarc"))

    # 5. ~/.mambarc
    _add_file(os.path.join(places.home, ".mambarc"))

    return files


def _parse_condarc_envs_dirs(condarc_path: str) -> list[str]:
    """Parse envs_dirs list from .condarc without any YAML library, expanding user and env vars."""
    dirs: list[str] = []
    try:
        if not os.path.isfile(longpath(condarc_path)):
            return dirs
        with open(longpath(condarc_path), "r", encoding="utf-8", errors="replace") as f:
            in_envs_dirs = False
            for line in f:
                raw_line = line
                if "#" in line:
                    line = line.split("#", 1)[0]
                line_str = line.strip()
                if not line_str:
                    continue
                if line_str.startswith("envs_dirs:"):
                    in_envs_dirs = True
                    rest = line_str[len("envs_dirs:"):].strip()
                    if rest.startswith("[") and rest.endswith("]"):
                        parts = rest[1:-1].split(",")
                        for p in parts:
                            p_clean = p.strip().strip("'\"")
                            if p_clean:
                                dirs.append(os.path.expandvars(os.path.expanduser(p_clean)))
                        in_envs_dirs = False
                    continue
                if in_envs_dirs:
                    if line_str.startswith("-"):
                        val = line_str.lstrip("-").strip().strip("'\"")
                        if val:
                            dirs.append(os.path.expandvars(os.path.expanduser(val)))
                    elif not raw_line.startswith((" ", "\t")):
                        in_envs_dirs = False
    except OSError:
        pass
    return dirs


def _parse_hatch_config(places: Places, environ: Mapping[str, str]) -> tuple[str, str | None]:
    """Parse Hatch data directory and custom virtual env directory from environment and config.toml."""
    hatch_data = environ.get("HATCH_DATA_DIR")
    if not hatch_data or not os.path.isabs(hatch_data):
        hatch_data = os.path.join(places.localappdata, "hatch")
    hatch_virtual_dir: str | None = None
    hatch_configs = []
    if environ.get("HATCH_CONFIG"):
        hatch_configs.append(environ["HATCH_CONFIG"])
    hatch_configs.append(os.path.join(places.localappdata, "hatch", "config.toml"))
    hatch_configs.append(os.path.join(places.appdata, "hatch", "config.toml"))

    for hcfg in hatch_configs:
        try:
            if os.path.isfile(longpath(hcfg)):
                with open(longpath(hcfg), "rb") as f:
                    hdata = tomllib.load(f)
                    d_sec = hdata.get("dirs", {})
                    if isinstance(d_sec, dict):
                        d_val = d_sec.get("data")
                        if d_val and isinstance(d_val, str):
                            cand_data = os.path.expandvars(os.path.expanduser(d_val))
                            if os.path.isabs(cand_data):
                                hatch_data = cand_data
                        env_sec = d_sec.get("env", {})
                        if isinstance(env_sec, dict):
                            v_val = env_sec.get("virtual")
                            if v_val and isinstance(v_val, str):
                                cand_v = os.path.expandvars(os.path.expanduser(v_val))
                                if os.path.isabs(cand_v):
                                    hatch_virtual_dir = cand_v
                break
        except Exception:
            pass

    return hatch_data, hatch_virtual_dir


def known_env_paths(
    places: Places,
    environ: Mapping[str, str],
    registry_paths: list[str] | None = None,
) -> list[str]:
    """Candidate environment folders (not yet probed), deduplicated by norm_key."""
    candidates: list[str] = []

    def _add_children(dir_path: str) -> None:
        try:
            if os.path.isdir(longpath(dir_path)):
                with os.scandir(longpath(dir_path)) as it:
                    for entry in it:
                        try:
                            if entry.is_dir():
                                candidates.append(entry.path)
                        except OSError:
                            continue
        except OSError:
            pass

    # 1. Every conda root, plus root\envs\*
    roots = find_conda_roots(places, environ, registry_paths=registry_paths)
    for root in roots:
        candidates.append(root)
        _add_children(os.path.join(root, "envs"))

    # 2. envs_dirs from every Conda config file (.condarc, condarc.d/*.yml, ~/.mambarc)
    config_files = _find_conda_config_files(places, environ, roots)
    for cf in config_files:
        eds = _parse_condarc_envs_dirs(cf)
        for ed in eds:
            candidates.append(ed)
            _add_children(ed)

    # Conda env vars CONDA_ENVS_PATH and CONDA_ENVS_DIRS (; separated on Windows)
    for ev_name in ("CONDA_ENVS_PATH", "CONDA_ENVS_DIRS"):
        ev_val = environ.get(ev_name)
        if ev_val:
            for part in ev_val.split(os.pathsep):
                part_clean = part.strip().strip("'\"")
                if part_clean:
                    exp = os.path.expandvars(os.path.expanduser(part_clean))
                    candidates.append(exp)
                    _add_children(exp)

    # 3. home\.conda\envs\*
    _add_children(os.path.join(places.home, ".conda", "envs"))

    # 4. micromamba / mamba roots (envs\* under MAMBA_ROOT_PREFIX, ~/micromamba [1.x default], %APPDATA%\mamba [2.x default])
    mamba_roots: list[str] = []
    mamba_root_prefix = environ.get("MAMBA_ROOT_PREFIX")
    if mamba_root_prefix:
        mamba_roots.append(os.path.expandvars(os.path.expanduser(mamba_root_prefix)))
    mamba_roots.append(os.path.join(places.home, "micromamba"))
    mamba_roots.append(os.path.join(places.appdata, "mamba"))
    mamba_roots.append(os.path.join(places.home, ".micromamba"))
    mamba_roots.append(os.path.join(places.localappdata, "mamba"))
    xdg_data = environ.get("XDG_DATA_HOME")
    if xdg_data and os.path.isabs(xdg_data):
        mamba_roots.append(os.path.join(xdg_data, "mamba"))

    for mr in mamba_roots:
        _add_children(os.path.join(mr, "envs"))

    # 5. Pixi global envs: %PIXI_HOME%\envs\* (default ~/.pixi/envs)
    pixi_home = environ.get("PIXI_HOME") or os.path.join(places.home, ".pixi")
    _add_children(os.path.join(os.path.expandvars(os.path.expanduser(pixi_home)), "envs"))

    # 6. Every line of environments.txt
    res = _read_env_lines(places.environments_txt)
    if res is not None:
        lines, _ = res
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                candidates.append(line)

    # 7. Tool venvs
    tool_parents = [
        # pipx: current default (%LOCALAPPDATA%\pipx\venvs), %PIPX_HOME%\venvs, legacy (~\pipx\venvs)
        os.path.join(places.localappdata, "pipx", "venvs"),
        os.path.join(places.home, "pipx", "venvs"),
        os.path.join(places.localappdata, "pipx", "pipx", "venvs"),
        os.path.join(places.home, ".local", "pipx", "venvs"),
        os.path.join(places.localappdata, "pypoetry", "Cache", "virtualenvs"),
        os.path.join(places.home, "Envs"),
    ]

    pipx_home = environ.get("PIPX_HOME")
    if pipx_home:
        tool_parents.append(os.path.join(os.path.expandvars(os.path.expanduser(pipx_home)), "venvs"))

    # Poetry config.toml virtualenvs.path
    poetry_config = os.path.join(places.appdata, "pypoetry", "config.toml")
    try:
        if os.path.isfile(longpath(poetry_config)):
            with open(longpath(poetry_config), "rb") as f:
                cfg = tomllib.load(f)
                vpath = cfg.get("virtualenvs", {}).get("path")
                if vpath and isinstance(vpath, str):
                    tool_parents.append(os.path.expandvars(os.path.expanduser(vpath)))
    except Exception:
        pass

    # POETRY_VIRTUALENVS_PATH
    poetry_vpath = environ.get("POETRY_VIRTUALENVS_PATH")
    if poetry_vpath:
        tool_parents.append(os.path.expandvars(os.path.expanduser(poetry_vpath)))

    # Pipenv WORKON_HOME (default ~/.virtualenvs)
    pipenv_workon = environ.get("WORKON_HOME") or os.path.join(places.home, ".virtualenvs")
    tool_parents.append(os.path.expandvars(os.path.expanduser(pipenv_workon)))

    # uv UV_TOOL_DIR (default %APPDATA%\uv\tools)
    uv_tool_dir = environ.get("UV_TOOL_DIR") or os.path.join(places.appdata, "uv", "tools")
    tool_parents.append(os.path.expandvars(os.path.expanduser(uv_tool_dir)))

    # PDM: shared venvs in %LOCALAPPDATA%\pdm\pdm\venvs, plus venv.location from config.toml
    tool_parents.append(os.path.join(places.localappdata, "pdm", "pdm", "venvs"))
    for pdm_cfg in (
        os.path.join(places.localappdata, "pdm", "pdm", "config.toml"),
        os.path.join(places.localappdata, "pdm", "config.toml"),
        os.path.join(places.appdata, "pdm", "config.toml"),
    ):
        try:
            if os.path.isfile(longpath(pdm_cfg)):
                with open(longpath(pdm_cfg), "rb") as f:
                    pdm_data = tomllib.load(f)
                    vloc = pdm_data.get("venv", {}).get("location") if isinstance(pdm_data.get("venv"), dict) else None
                    if not vloc:
                        vloc = pdm_data.get("venv.location")
                    if vloc and isinstance(vloc, str):
                        tool_parents.append(os.path.expandvars(os.path.expanduser(vloc)))
                break
        except Exception:
            pass

    # Rye: %RYE_HOME% (default ~/.rye): tools\* are envs
    rye_home = environ.get("RYE_HOME") or os.path.join(places.home, ".rye")
    tool_parents.append(os.path.join(os.path.expandvars(os.path.expanduser(rye_home)), "tools"))

    for tp in tool_parents:
        _add_children(tp)

    # 8. Hatch: <data>\env\virtual, bounded scandir at most 3 levels down
    hatch_data, hatch_virtual_dir = _parse_hatch_config(places, environ)
    if not hatch_virtual_dir:
        hatch_virtual_dir = os.path.join(hatch_data, "env", "virtual")

    # Bounded scandir at most 3 levels down looking for pyvenv.cfg
    try:
        if os.path.isdir(longpath(hatch_virtual_dir)):
            with os.scandir(longpath(hatch_virtual_dir)) as it1:
                for e1 in it1:
                    try:
                        if not e1.is_dir():
                            continue
                        if os.path.isfile(os.path.join(e1.path, "pyvenv.cfg")):
                            candidates.append(e1.path)
                            continue
                        with os.scandir(longpath(e1.path)) as it2:
                            for e2 in it2:
                                try:
                                    if not e2.is_dir():
                                        continue
                                    if os.path.isfile(os.path.join(e2.path, "pyvenv.cfg")):
                                        candidates.append(e2.path)
                                        continue
                                    # Level 3 limit: do not scan deeper than level 3
                                    with os.scandir(longpath(e2.path)) as it3:
                                        for e3 in it3:
                                            try:
                                                if e3.is_dir() and os.path.isfile(os.path.join(e3.path, "pyvenv.cfg")):
                                                    candidates.append(e3.path)
                                            except OSError:
                                                continue
                                except OSError:
                                    continue
                    except OSError:
                        continue
    except OSError:
        pass

    # Filter: only folders that exist, deduplicated by norm_key
    seen: set[str] = set()
    result: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        try:
            if not os.path.isdir(longpath(cand)):
                continue
            k = norm_key(cand)
            if k not in seen:
                seen.add(k)
                result.append(cand)
        except OSError:
            continue

    return result


def _is_windows_apps(path: str) -> bool:
    try:
        parts = norm_key(path).split("\\")
        return "windowsapps" in parts
    except Exception:
        return "windowsapps" in path.lower()


def python_install_paths(
    places: Places,
    registry_paths: list[str],
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Find installed Python directories, skipping WindowsApps and validating with python_install()."""
    candidates: list[str] = list(registry_paths)
    env_map = environ if environ is not None else os.environ

    def _add_children(dir_path: str, prefix_filter: str = "") -> None:
        try:
            if os.path.isdir(longpath(dir_path)):
                with os.scandir(longpath(dir_path)) as it:
                    for entry in it:
                        try:
                            if entry.is_dir():
                                if not prefix_filter or entry.name.lower().startswith(prefix_filter.lower()):
                                    candidates.append(entry.path)
                        except OSError:
                            continue
        except OSError:
            pass

    # home\.pyenv\pyenv-win\versions\*
    _add_children(os.path.join(places.home, ".pyenv", "pyenv-win", "versions"))

    # localappdata\Python\pythoncore-*
    _add_children(os.path.join(places.localappdata, "Python"), prefix_filter="pythoncore-")

    # localappdata\Programs\Python\*
    _add_children(os.path.join(places.localappdata, "Programs", "Python"))

    # uv-managed Pythons in %APPDATA%\uv\python\* (UV_PYTHON_INSTALL_DIR)
    uv_py_dir = env_map.get("UV_PYTHON_INSTALL_DIR") or os.path.join(places.appdata, "uv", "python")
    _add_children(uv_py_dir)

    # Hatch Pythons in <hatch_data>\pythons\<name>\python\* or ...\python\install\*
    h_data, _ = _parse_hatch_config(places, env_map)
    h_py_dir = os.path.join(h_data, "pythons")
    try:
        if os.path.isdir(longpath(h_py_dir)):
            with os.scandir(longpath(h_py_dir)) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            candidates.append(entry.path)
                            candidates.append(os.path.join(entry.path, "python"))
                            candidates.append(os.path.join(entry.path, "python", "install"))
                    except OSError:
                        continue
    except OSError:
        pass

    # Rye Pythons in %RYE_HOME%\py\* (default ~/.rye\py\*)
    rye_h = env_map.get("RYE_HOME") or os.path.join(places.home, ".rye")
    rye_py_dir = os.path.join(os.path.expandvars(os.path.expanduser(rye_h)), "py")
    try:
        if os.path.isdir(longpath(rye_py_dir)):
            with os.scandir(longpath(rye_py_dir)) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            candidates.append(entry.path)
                            candidates.append(os.path.join(entry.path, "install"))
                    except OSError:
                        continue
    except OSError:
        pass

    seen: set[str] = set()
    result: list[str] = []
    for cand in candidates:
        if not cand:
            continue
        if _is_windows_apps(cand):
            continue
        try:
            k = norm_key(cand)
            if k in seen:
                continue
            env = python_install(cand)
            if env is not None and env.kind == Kind.PYTHON:
                seen.add(k)
                result.append(cand)
        except OSError:
            continue

    return result


def registry_install_paths() -> list[str]:
    """Read Python install paths from Windows registry (HKCU and HKLM)."""
    paths: list[str] = []
    seen: set[str] = set()

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Python"),
        (winreg.HKEY_CURRENT_USER, r"Software\WOW6432Node\Python"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Python"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Python"),
    ]

    for root_hkey, subkey_path in roots:
        try:
            with winreg.OpenKey(root_hkey, subkey_path) as python_key:
                company_idx = 0
                while True:
                    try:
                        company = winreg.EnumKey(python_key, company_idx)
                        company_idx += 1
                    except OSError:
                        break

                    if company.lower() == "pylauncher":
                        continue

                    try:
                        with winreg.OpenKey(python_key, company) as company_key:
                            tag_idx = 0
                            while True:
                                try:
                                    tag = winreg.EnumKey(company_key, tag_idx)
                                    tag_idx += 1
                                except OSError:
                                    break

                                try:
                                    with winreg.OpenKey(company_key, rf"{tag}\InstallPath") as inst_key:
                                        try:
                                            val, _ = winreg.QueryValueEx(inst_key, "")
                                            if val and isinstance(val, str):
                                                val = val.strip().rstrip("\\/")
                                                if val:
                                                    k = norm_key(val)
                                                    if k not in seen:
                                                        seen.add(k)
                                                        paths.append(val)
                                        except OSError:
                                            pass
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except OSError:
            pass

    return paths
