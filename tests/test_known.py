import os
import unittest
from unittest import mock

from tests import fakes
from venvdiscover.classify import probe, python_install
from venvdiscover.known import (
    find_conda_roots,
    known_env_paths,
    python_install_paths,
    registry_install_paths,
)
from venvdiscover.model import Kind, longpath, norm_key


def _ansi_only(name: str) -> bool:
    """True if name round-trips through the ANSI code page (mbcs) and those bytes are not valid UTF-8."""
    try:
        raw = name.encode("mbcs")
    except UnicodeEncodeError:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs") == name
    return False


class KnownTests(unittest.TestCase):
    def test_roots_from_home_names(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            root = fakes.make_conda_root(places.home, "miniconda3_custom")
            roots = find_conda_roots(places, {})
            self.assertEqual(len(roots), 1)
            self.assertEqual(norm_key(roots[0]), norm_key(root))

    def test_stale_environments_txt(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            root = fakes.make_conda_root(tmp, "miniforge")
            ea = fakes.make_conda_env(root, "a")
            eb = fakes.make_conda_env(root, "b")
            ec = fakes.make_conda_env(root, "c")

            # environments.txt only lists 'a'
            fakes.write_file(places.environments_txt, f"{ea}\n")

            # 1. find_conda_roots: 'a' is a plain env, not a conda base.
            # Only root is returned; 'a' is filtered out by probe() == Kind.CONDA_BASE.
            roots = find_conda_roots(places, {})
            norm_roots = [norm_key(r) for r in roots]
            self.assertEqual(len(roots), 1)
            self.assertIn(norm_key(root), norm_roots)
            self.assertNotIn(norm_key(ea), norm_roots)

            # 2. known_env_paths expands root\envs\* so all of a, b, c are included
            paths = known_env_paths(places, {})
            norm_paths = {norm_key(p) for p in paths}
            self.assertIn(norm_key(ea), norm_paths)
            self.assertIn(norm_key(eb), norm_paths)
            self.assertIn(norm_key(ec), norm_paths)

    def test_condarc_envs_dirs(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            custom_dir = os.path.join(tmp, "custom_envs")
            c1 = os.path.join(custom_dir, "c1")
            c2 = os.path.join(custom_dir, "c2")
            os.makedirs(c1, exist_ok=True)
            os.makedirs(c2, exist_ok=True)

            # Directory under user home to test ~ expansion
            tilde_dir = os.path.join(places.home, "myenvs", "c_tilde")
            os.makedirs(tilde_dir, exist_ok=True)

            condarc = os.path.join(places.home, ".condarc")
            fakes.write_file(
                condarc,
                "channels:\n  - conda-forge\nenvs_dirs:\n  - ~/myenvs\n  - %CUSTOM_ENVS_VAR%\n",
            )

            with mock.patch.dict(os.environ, {
                "USERPROFILE": places.home,
                "HOME": places.home,
                "CUSTOM_ENVS_VAR": custom_dir,
            }):
                paths = known_env_paths(places, {})
                norm_paths = {norm_key(p) for p in paths}
                self.assertIn(norm_key(c1), norm_paths)
                self.assertIn(norm_key(c2), norm_paths)
                self.assertIn(norm_key(tilde_dir), norm_paths)

    def test_conda_exe_env(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            # Test Scripts/conda.exe via CONDA_EXE
            root1 = fakes.make_conda_root(tmp, "custom_dist_1")
            exe1 = os.path.join(root1, "Scripts", "conda.exe")
            fakes.write_file(exe1)

            roots = find_conda_roots(places, {"CONDA_EXE": exe1})
            norm_roots = [norm_key(r) for r in roots]
            self.assertIn(norm_key(root1), norm_roots)

            # Test condabin/conda.bat via PATH and which
            root2 = fakes.make_conda_root(tmp, "custom_dist_2")
            condabin = os.path.join(root2, "condabin")
            fakes.write_file(os.path.join(condabin, "conda.bat"))
            roots2 = find_conda_roots(places, {"PATH": condabin})
            norm_roots2 = [norm_key(r) for r in roots2]
            self.assertIn(norm_key(root2), norm_roots2)

    def test_python_installs(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            p_prog = fakes.make_python_install(
                os.path.join(places.localappdata, "Programs", "Python"), "Python311", version="3.11.9"
            )
            p_pyenv = fakes.make_python_install(
                os.path.join(places.home, ".pyenv", "pyenv-win", "versions"), "3.11.9", version="3.11.9"
            )
            c_root = fakes.make_conda_root(tmp, "fake_conda_root")
            # Build a full python install inside WindowsApps to verify _is_windows_apps guard
            wa_alias = fakes.make_python_install(
                os.path.join(places.localappdata, "Microsoft", "WindowsApps"), "PythonSoftwareFoundation.Python.3.11", version="3.11.9"
            )

            installs = python_install_paths(places, [c_root, wa_alias])
            norm_inst = [norm_key(p) for p in installs]

            self.assertIn(norm_key(p_prog), norm_inst)
            self.assertIn(norm_key(p_pyenv), norm_inst)
            self.assertNotIn(norm_key(c_root), norm_inst)
            self.assertNotIn(norm_key(wa_alias), norm_inst)

    def test_registry_smoke(self):
        paths = registry_install_paths()
        self.assertIsInstance(paths, list)
        for p in paths:
            self.assertIsInstance(p, str)

    def test_environments_txt_ansi_code_page(self):
        """environments.txt written in this PC's ANSI code page (not UTF-8) is still read.

        The name must exist in the local code page (cp1250 on Polish Windows, cp1252 on English) and its
        bytes must not be valid UTF-8, so the reader really takes the code-page fallback."""
        name = next((n for n in ("źródło", "café", "naïve") if _ansi_only(n)), None)
        if name is None:
            self.skipTest("this PC's ANSI code page is UTF-8 or can't encode the sample names")
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env_path = os.path.join(tmp, name, "env")
            fakes.make_venv(os.path.join(tmp, name), "env")

            raw_bytes = f"{env_path}\n".encode("mbcs")
            os.makedirs(os.path.dirname(longpath(places.environments_txt)), exist_ok=True)
            with open(longpath(places.environments_txt), "wb") as f:
                f.write(raw_bytes)

            paths = known_env_paths(places, {})
            norm_paths = {norm_key(p) for p in paths}
            self.assertIn(norm_key(env_path), norm_paths)

    def test_condarc_flow_format(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            a_dir = os.path.join(places.home, "a", "env1")
            os.makedirs(a_dir, exist_ok=True)
            b_dir = os.path.join(tmp, "var_dir", "env2")
            os.makedirs(b_dir, exist_ok=True)

            condarc = os.path.join(places.home, ".condarc")
            fakes.write_file(condarc, "envs_dirs: [~/a, %VAR_DIR%]\n")

            with mock.patch.dict(os.environ, {
                "USERPROFILE": places.home,
                "HOME": places.home,
                "VAR_DIR": os.path.join(tmp, "var_dir"),
            }):
                paths = known_env_paths(places, {})
                norm_paths = {norm_key(p) for p in paths}
                self.assertIn(norm_key(a_dir), norm_paths)
                self.assertIn(norm_key(b_dir), norm_paths)



    def test_pixi_probe_and_known_places(self):
        """Pixi envs with conda-meta/pixi or package records (*.json) are recognized; empty conda-meta is not."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. pixi env with conda-meta/pixi and no history
            pixi_env = os.path.join(tmp, "my_pixi_env")
            os.makedirs(os.path.join(pixi_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(pixi_env, "conda-meta", "pixi"), "pixi manifest")
            env1 = probe(pixi_env, places)
            self.assertIsNotNone(env1)
            self.assertEqual(env1.kind, Kind.CONDA)
            self.assertIsNone(env1.conda_root)

            # 2. conda env with package records (*.json) and no history or pixi
            json_env = os.path.join(tmp, "my_json_env")
            os.makedirs(os.path.join(json_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(json_env, "conda-meta", "python-3.11.0.json"), "{}")
            env2 = probe(json_env, places)
            self.assertIsNotNone(env2)
            self.assertEqual(env2.kind, Kind.CONDA)

            # 3. empty conda-meta directory is NOT an env
            empty_env = os.path.join(tmp, "empty_meta_folder")
            os.makedirs(os.path.join(empty_env, "conda-meta"), exist_ok=True)
            self.assertIsNone(probe(empty_env, places))

            # 4. Known places: default ~/.pixi/envs/*
            def_pixi_env = os.path.join(places.home, ".pixi", "envs", "default_pixi")
            os.makedirs(os.path.join(def_pixi_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(def_pixi_env, "conda-meta", "pixi"), "")
            paths = known_env_paths(places, {})
            self.assertTrue(any(norm_key(p) == norm_key(def_pixi_env) for p in paths))

            # 5. Known places: override PIXI_HOME
            custom_pixi = os.path.join(tmp, "custom_pixi_home")
            custom_env = os.path.join(custom_pixi, "envs", "custom_env")
            os.makedirs(os.path.join(custom_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(custom_env, "conda-meta", "pixi"), "")
            paths_override = known_env_paths(places, {"PIXI_HOME": custom_pixi})
            self.assertTrue(any(norm_key(p) == norm_key(custom_env) for p in paths_override))

    def test_micromamba_and_mamba_roots(self):
        """Mamba roots have envs under envs/ but no conda-meta; envs have conda_root=None and root isn't listed."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. 1.x real default: ~/micromamba/envs/* (libmamba configuration.cpp:740)
            mm_root = os.path.join(places.home, "micromamba")
            mm_env = os.path.join(mm_root, "envs", "mm_env")
            os.makedirs(os.path.join(mm_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(mm_env, "conda-meta", "history"), "history content")

            # 2. 2.x real default: %APPDATA%\mamba\envs/* (libmamba configuration.cpp:746, util/environment.cpp:213)
            mamba2_root = os.path.join(places.appdata, "mamba")
            mamba2_env = os.path.join(mamba2_root, "envs", "m2_env")
            os.makedirs(os.path.join(mamba2_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(mamba2_env, "conda-meta", "history"), "history content")

            # 3. Legacy / alternate paths: ~/.micromamba and %LOCALAPPDATA%\mamba
            legacy_mm = os.path.join(places.home, ".micromamba", "envs", "legacy_mm_env")
            os.makedirs(os.path.join(legacy_mm, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(legacy_mm, "conda-meta", "history"), "history content")

            local_mamba = os.path.join(places.localappdata, "mamba", "envs", "local_mamba_env")
            os.makedirs(os.path.join(local_mamba, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(local_mamba, "conda-meta", "history"), "history content")

            # 4. XDG_DATA_HOME override for 2.x (libmamba util/environment.cpp:213-219)
            xdg_dir = os.path.join(tmp, "xdg_data")
            xdg_env = os.path.join(xdg_dir, "mamba", "envs", "xdg_mamba_env")
            os.makedirs(os.path.join(xdg_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(xdg_env, "conda-meta", "history"), "history content")

            # 5. Override MAMBA_ROOT_PREFIX
            custom_mamba = os.path.join(tmp, "custom_mamba")
            custom_mamba_env = os.path.join(custom_mamba, "envs", "custom_env")
            os.makedirs(os.path.join(custom_mamba_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(custom_mamba_env, "conda-meta", "history"), "history content")

            environ = {
                "MAMBA_ROOT_PREFIX": custom_mamba,
                "XDG_DATA_HOME": xdg_dir,
            }
            paths = known_env_paths(places, environ)
            self.assertTrue(any(norm_key(p) == norm_key(mm_env) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(mamba2_env) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(legacy_mm) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(local_mamba) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(xdg_env) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(custom_mamba_env) for p in paths))

            # Mamba root has no conda-meta, so probe() gives conda_root=None
            env_probed = probe(mm_env, places)
            self.assertIsNotNone(env_probed)
            self.assertEqual(env_probed.kind, Kind.CONDA)
            self.assertIsNone(env_probed.conda_root)

            # find_conda_roots does NOT list mm_root, mamba2_root or custom_mamba
            roots = find_conda_roots(places, environ)
            self.assertFalse(any(norm_key(r) == norm_key(mm_root) for r in roots))
            self.assertFalse(any(norm_key(r) == norm_key(mamba2_root) for r in roots))
            self.assertFalse(any(norm_key(r) == norm_key(custom_mamba) for r in roots))

            # 4. ~/.mambarc envs_dirs
            mambarc_dir = os.path.join(tmp, "mambarc_envs")
            fakes.make_venv(mambarc_dir, "mamba_v1")
            mambarc_file = os.path.join(places.home, ".mambarc")
            fakes.write_file(mambarc_file, f"envs_dirs:\n  - {mambarc_dir}\n")

            paths_mambarc = known_env_paths(places, {})
            self.assertTrue(any("mamba_v1" in p for p in paths_mambarc))

    def test_every_conda_config_file_and_env_vars(self):
        """Read envs_dirs from every Conda config location and env vars CONDA_ENVS_PATH, CONDA_ENVS_DIRS."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. System: ProgramData\conda\.condarc and condarc.d\extra.yml
            sys_dir = os.path.join(places.programdata, "conda")
            sys_envs = os.path.join(tmp, "sys_envs")
            fakes.make_venv(sys_envs, "sys_env_1")
            fakes.write_file(os.path.join(sys_dir, ".condarc"), f"envs_dirs:\n  - {sys_envs}\n")

            d_envs = os.path.join(tmp, "d_envs")
            fakes.make_venv(d_envs, "d_env_1")
            os.makedirs(os.path.join(sys_dir, "condarc.d"), exist_ok=True)
            fakes.write_file(os.path.join(sys_dir, "condarc.d", "more.yml"), f"envs_dirs:\n  - {d_envs}\n")

            # 2. User ~/.config/conda/condarc
            user_cfg_dir = os.path.join(places.home, ".config", "conda")
            user_envs = os.path.join(tmp, "user_config_envs")
            fakes.make_venv(user_envs, "user_env_1")
            os.makedirs(user_cfg_dir, exist_ok=True)
            fakes.write_file(os.path.join(user_cfg_dir, "condarc"), f"envs_dirs:\n  - {user_envs}\n")

            # 3. User ~/.conda/.condarc
            user_conda_dir = os.path.join(places.home, ".conda")
            user_conda_envs = os.path.join(tmp, "user_dotconda_envs")
            fakes.make_venv(user_conda_envs, "dotconda_env_1")
            os.makedirs(user_conda_dir, exist_ok=True)
            fakes.write_file(os.path.join(user_conda_dir, ".condarc"), f"envs_dirs:\n  - {user_conda_envs}\n")

            # 4. CONDARC env var
            custom_condarc = os.path.join(tmp, "custom_condarc.yaml")
            custom_envs = os.path.join(tmp, "custom_condarc_envs")
            fakes.make_venv(custom_envs, "custom_env_1")
            fakes.write_file(custom_condarc, f"envs_dirs:\n  - {custom_envs}\n")

            # 5. CONDA_ENVS_PATH and CONDA_ENVS_DIRS
            ev_dir1 = os.path.join(tmp, "ev_envs1")
            ev_dir2 = os.path.join(tmp, "ev_envs2")
            fakes.make_venv(ev_dir1, "ev1_env")
            fakes.make_venv(ev_dir2, "ev2_env")

            # 6. Per-root .condarc and condarc.d\extra.yaml (mutant C5 guard)
            root_dir = fakes.make_conda_root(tmp, "my_conda_root")
            root_envs = os.path.join(tmp, "root_condarc_envs")
            fakes.make_venv(root_envs, "root_env_1")
            fakes.write_file(os.path.join(root_dir, ".condarc"), f"envs_dirs:\n  - {root_envs}\n")

            root_d_envs = os.path.join(tmp, "root_d_envs")
            fakes.make_venv(root_d_envs, "root_d_env_1")
            os.makedirs(os.path.join(root_dir, "condarc.d"), exist_ok=True)
            fakes.write_file(os.path.join(root_dir, "condarc.d", "extra.yaml"), f"envs_dirs:\n  - {root_d_envs}\n")

            environ = {
                "CONDARC": custom_condarc,
                "CONDA_ENVS_PATH": ev_dir1,
                "CONDA_ENVS_DIRS": f"{ev_dir2};C:\\non_existent_path_xyz",
            }

            paths = known_env_paths(places, environ, registry_paths=[root_dir])
            self.assertTrue(any("sys_env_1" in p for p in paths))
            self.assertTrue(any("d_env_1" in p for p in paths))
            self.assertTrue(any("user_env_1" in p for p in paths))
            self.assertTrue(any("dotconda_env_1" in p for p in paths))
            self.assertTrue(any("custom_env_1" in p for p in paths))
            self.assertTrue(any("ev1_env" in p for p in paths))
            self.assertTrue(any("ev2_env" in p for p in paths))
            self.assertTrue(any("root_env_1" in p for p in paths))
            self.assertTrue(any("root_d_env_1" in p for p in paths))

    def test_registry_conda_roots(self):
        """Injected registry paths: conda base is detected, plain Python is not added as conda root."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Injected Anaconda root (with conda-meta and condabin)
            conda_reg_root = os.path.join(tmp, "reg_anaconda")
            os.makedirs(os.path.join(conda_reg_root, "conda-meta"), exist_ok=True)
            os.makedirs(os.path.join(conda_reg_root, "condabin"), exist_ok=True)
            fakes.write_file(os.path.join(conda_reg_root, "conda-meta", "history"), "history")

            # Injected plain Python install (python.exe + Lib/os.py)
            py_reg_root = fakes.make_python_install(tmp, "reg_python")

            registry_paths = [conda_reg_root, py_reg_root]

            # 1. find_conda_roots: finds Anaconda root, drops plain Python
            roots = find_conda_roots(places, {}, registry_paths=registry_paths)
            self.assertTrue(any(norm_key(r) == norm_key(conda_reg_root) for r in roots))
            self.assertFalse(any(norm_key(r) == norm_key(py_reg_root) for r in roots))

            # 2. python_install_paths: finds plain Python, drops Conda root
            py_installs = python_install_paths(places, registry_paths=registry_paths)
            self.assertTrue(any(norm_key(p) == norm_key(py_reg_root) for p in py_installs))
            self.assertFalse(any(norm_key(p) == norm_key(conda_reg_root) for p in py_installs))

    def test_pipx_defaults_and_override(self):
        """Pipx venvs found at %LOCALAPPDATA%\\pipx\\venvs, ~\\pipx\\venvs, and %PIPX_HOME%\\venvs."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Default: %LOCALAPPDATA%\pipx\venvs
            p1 = os.path.join(places.localappdata, "pipx", "venvs")
            fakes.make_venv(p1, "app_modern")

            # Legacy: ~\pipx\venvs
            p2 = os.path.join(places.home, "pipx", "venvs")
            fakes.make_venv(p2, "app_legacy")

            # Override: %PIPX_HOME%\venvs
            custom_pipx = os.path.join(tmp, "custom_pipx")
            p3 = os.path.join(custom_pipx, "venvs")
            fakes.make_venv(p3, "app_override")

            paths = known_env_paths(places, {"PIPX_HOME": custom_pipx})
            self.assertTrue(any("app_modern" in p for p in paths))
            self.assertTrue(any("app_legacy" in p for p in paths))
            self.assertTrue(any("app_override" in p for p in paths))

    def test_hatch_envs_and_pythons(self):
        """Hatch envs under <data>\\env\\virtual are bounded to <= 3 levels; Pythons in <data>\\pythons."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            hatch_data = os.path.join(places.localappdata, "hatch")
            virt_dir = os.path.join(hatch_data, "env", "virtual")

            # Level 1 env
            e1 = os.path.join(virt_dir, "proj1")
            fakes.write_file(os.path.join(e1, "pyvenv.cfg"), "home = C:\\fake\n")

            # Level 2 env
            e2 = os.path.join(virt_dir, "proj2", "default")
            fakes.write_file(os.path.join(e2, "pyvenv.cfg"), "home = C:\\fake\n")

            # Level 3 env
            e3 = os.path.join(virt_dir, "proj3", "test", "hash123")
            fakes.write_file(os.path.join(e3, "pyvenv.cfg"), "home = C:\\fake\n")

            # Level 4 env (hard bound: must NOT be found)
            e4 = os.path.join(virt_dir, "proj4", "matrix", "py311", "deep_hash")
            fakes.write_file(os.path.join(e4, "pyvenv.cfg"), "home = C:\\fake\n")

            # Hatch Pythons in real layouts: <py_dir>\<name>\python and ...\python\install
            py_dir = os.path.join(hatch_data, "pythons")
            hatch_py = fakes.make_python_install(os.path.join(py_dir, "3.12.0"), "python")
            hatch_full_py = fakes.make_python_install(os.path.join(py_dir, "3.13.0", "python"), "install")

            paths = known_env_paths(places, {})
            self.assertTrue(any(norm_key(p) == norm_key(e1) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(e2) for p in paths))
            self.assertTrue(any(norm_key(p) == norm_key(e3) for p in paths))
            self.assertFalse(any(norm_key(p) == norm_key(e4) for p in paths))

            # Hatch Pythons listed in python_install_paths as locked Python installs
            pythons = python_install_paths(places, registry_paths=[])
            self.assertTrue(any(norm_key(p) == norm_key(hatch_py) for p in pythons))
            self.assertTrue(any(norm_key(p) == norm_key(hatch_full_py) for p in pythons))

            # Override via config.toml [dirs.env] virtual and [dirs] data
            custom_cfg = os.path.join(tmp, "hatch_custom_config.toml")
            custom_data = os.path.join(tmp, "custom_hatch_data")
            custom_virt = os.path.join(tmp, "custom_virtual_envs")
            fakes.write_file(
                custom_cfg,
                f'[dirs]\ndata = "{custom_data.replace(chr(92), chr(47))}"\n[dirs.env]\nvirtual = "{custom_virt.replace(chr(92), chr(47))}"\n',
            )

            custom_e1 = os.path.join(custom_virt, "custom_p1")
            fakes.write_file(os.path.join(custom_e1, "pyvenv.cfg"), "home = C:\\fake\n")
            custom_py = fakes.make_python_install(os.path.join(custom_data, "pythons", "3.11.5"), "python")

            paths_override = known_env_paths(places, {"HATCH_CONFIG": custom_cfg})
            self.assertTrue(any(norm_key(p) == norm_key(custom_e1) for p in paths_override))

            pythons_override = python_install_paths(places, registry_paths=[], environ={"HATCH_CONFIG": custom_cfg})
            self.assertTrue(any(norm_key(p) == norm_key(custom_py) for p in pythons_override))

            # Relative paths in config.toml must be ignored (relative to project, not app root)
            rel_cfg = os.path.join(tmp, "hatch_rel_config.toml")
            fakes.write_file(
                rel_cfg,
                '[dirs]\ndata = "relative_data"\n[dirs.env]\nvirtual = "relative_virtual"\n',
            )
            paths_rel = known_env_paths(places, {"HATCH_CONFIG": rel_cfg})
            self.assertTrue(any(norm_key(p) == norm_key(e1) for p in paths_rel))
            pythons_rel = python_install_paths(places, registry_paths=[], environ={"HATCH_CONFIG": rel_cfg})
            self.assertTrue(any(norm_key(p) == norm_key(hatch_py) for p in pythons_rel))

    def test_pdm_envs(self):
        """PDM shared venvs in %LOCALAPPDATA%\\pdm\\pdm\\venvs and venv.location from config.toml."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Default shared venvs
            pdm_def = os.path.join(places.localappdata, "pdm", "pdm", "venvs")
            fakes.make_venv(pdm_def, "pdm_default_env")

            # Override via config.toml
            pdm_cfg = os.path.join(places.localappdata, "pdm", "pdm", "config.toml")
            custom_pdm_dir = os.path.join(tmp, "my_pdm_venvs")
            fakes.make_venv(custom_pdm_dir, "pdm_custom_env")
            fakes.write_file(
                pdm_cfg,
                f'[venv]\nlocation = "{custom_pdm_dir.replace(chr(92), chr(47))}"\n',
            )

            paths = known_env_paths(places, {})
            self.assertTrue(any("pdm_default_env" in p for p in paths))
            self.assertTrue(any("pdm_custom_env" in p for p in paths))

    def test_rye_envs_and_pythons(self):
        """Rye tools/* are envs and py/* are locked Python installs."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Default ~/.rye
            rye_tools = os.path.join(places.home, ".rye", "tools")
            fakes.make_venv(rye_tools, "tool1")

            rye_py_dir = os.path.join(places.home, ".rye", "py")
            rye_py = fakes.make_python_install(rye_py_dir, "cpython@3.11.0")
            # Standalone python layout: <ver>\install\python.exe
            rye_nested_py = fakes.make_python_install(os.path.join(rye_py_dir, "cpython@3.12.1"), "install")

            # Override RYE_HOME
            custom_rye = os.path.join(tmp, "custom_rye")
            fakes.make_venv(os.path.join(custom_rye, "tools"), "custom_tool")
            custom_rye_py = fakes.make_python_install(os.path.join(custom_rye, "py"), "cpython@3.12.0")
            custom_rye_nested_py = fakes.make_python_install(
                os.path.join(custom_rye, "py", "cpython@3.13.0"), "install"
            )

            paths_default = known_env_paths(places, {})
            self.assertTrue(any("tool1" in p for p in paths_default))

            paths_override = known_env_paths(places, {"RYE_HOME": custom_rye})
            self.assertTrue(any("custom_tool" in p for p in paths_override))

            pythons_default = python_install_paths(places, registry_paths=[])
            self.assertTrue(any(norm_key(p) == norm_key(rye_py) for p in pythons_default))
            self.assertTrue(any(norm_key(p) == norm_key(rye_nested_py) for p in pythons_default))

            pythons_override = python_install_paths(places, registry_paths=[], environ={"RYE_HOME": custom_rye})
            self.assertTrue(any(norm_key(p) == norm_key(custom_rye_py) for p in pythons_override))
            self.assertTrue(any(norm_key(p) == norm_key(custom_rye_nested_py) for p in pythons_override))

    def test_registry_not_consulted_without_explicit_parameter(self):
        """find_conda_roots and known_env_paths must not touch the real registry when registry_paths is omitted."""
        with mock.patch("venvdiscover.known.registry_install_paths", side_effect=AssertionError("Leaked registry read")):
            with fakes.tmpdir() as tmp:
                places = fakes.fake_places(tmp)
                roots = find_conda_roots(places, {})
                self.assertEqual(roots, [])
                paths = known_env_paths(places, {})
                self.assertIsInstance(paths, list)


if __name__ == "__main__":
    unittest.main()
