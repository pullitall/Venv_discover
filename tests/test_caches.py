"""Tests for discovering and safely cleaning uv, poetry, pipenv and pip package caches."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from unittest import mock

from tests import fakes, tk_support
from venvdiscover.caches import (
    Cache,
    check_cache_safety,
    clean_cache,
    find_caches,
    measure_cache,
    measure_cache_usage,
    safe_delete_cache_contents,
    _pip_ini_cache_dir,
)
from venvdiscover.classify import probe, python_install
from venvdiscover.known import known_env_paths, python_install_paths
from venvdiscover.model import Kind, LOCKED_KINDS, longpath, norm_key
from venvdiscover.ui.app import UIAction
from venvdiscover.ui.caches_dialog import CacheCleanDialog, CachesDialog


def pump(root: tk.Misc, timeout: float = 4.0, condition=None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if condition and condition():
            return
        time.sleep(0.01)


class CachesTests(unittest.TestCase):
    def test_measure_cache_usage_counts_files_linked_into_envs_as_shared(self):
        """uv hard-links files from its cache into envs: cleaning the cache frees only the files no env links to."""
        with fakes.tmpdir() as tmp:
            cache = os.path.join(tmp, "uv_cache")
            env = os.path.join(tmp, "env")
            fakes.write_file(os.path.join(cache, "archive", "pkg.pyd"), b"w" * 20000)
            fakes.write_file(os.path.join(cache, "only_here.bin"), b"c" * 5000)
            os.makedirs(env, exist_ok=True)
            os.link(os.path.join(cache, "archive", "pkg.pyd"), os.path.join(env, "pkg.pyd"))

            total, unique, _ = measure_cache_usage(cache)
            self.assertEqual(total, 25000)
            self.assertEqual(unique, 5000)
            self.assertEqual(measure_cache(cache)[0], 25000)

    def test_find_caches_defaults(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            uv_dir = os.path.join(places.localappdata, "uv", "cache")
            poetry_dir = os.path.join(places.localappdata, "pypoetry", "Cache")
            pipenv_dir = os.path.join(places.localappdata, "pipenv", "pipenv", "Cache")
            pip_dir = os.path.join(places.localappdata, "pip", "Cache")

            for d in (uv_dir, poetry_dir, pipenv_dir, pip_dir):
                os.makedirs(longpath(d), exist_ok=True)
                fakes.write_file(os.path.join(d, "file.txt"), "cache content 12345")

            caches = find_caches(places, environ={"PATH": ""})
            self.assertEqual(len(caches), 4)

            tools = [c.tool for c in caches]
            self.assertEqual(tools, ["uv", "poetry", "pipenv", "pip"])

            for c in caches:
                self.assertGreater(c.size, 0)
                self.assertEqual(c.size_unique, c.size)  # nothing linked into envs here
                self.assertGreater(c.last_modified, 0.0)
                self.assertEqual(c.clean_method, "safe permanent delete")

            # Remove uv cache, ensure it is not listed
            shutil.rmtree(longpath(uv_dir))
            caches_after = find_caches(places, environ={"PATH": ""})
            tools_after = [c.tool for c in caches_after]
            self.assertNotIn("uv", tools_after)
            self.assertEqual(len(caches_after), 3)

    def test_find_caches_overrides(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            uv_custom = os.path.join(tmp, "custom_uv_cache")
            poetry_custom = os.path.join(tmp, "custom_poetry_cache")
            pipenv_custom = os.path.join(tmp, "custom_pipenv_cache")
            pip_custom = os.path.join(tmp, "custom_pip_cache")

            for d in (uv_custom, poetry_custom, pipenv_custom, pip_custom):
                os.makedirs(longpath(d), exist_ok=True)
                fakes.write_file(os.path.join(d, "data.bin"), "overridden")

            env = {
                "UV_CACHE_DIR": uv_custom,
                "POETRY_CACHE_DIR": poetry_custom,
                "PIPENV_CACHE_DIR": pipenv_custom,
                "PIP_CACHE_DIR": pip_custom,
                "PATH": "",
            }

            caches = find_caches(places, environ=env)
            self.assertEqual(len(caches), 4)

            paths = {c.tool: norm_key(c.path) for c in caches}
            self.assertEqual(paths["uv"], norm_key(uv_custom))
            self.assertEqual(paths["poetry"], norm_key(poetry_custom))
            self.assertEqual(paths["pipenv"], norm_key(pipenv_custom))
            self.assertEqual(paths["pip"], norm_key(pip_custom))

    def test_pip_ini_cache_dir(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            custom_pip = os.path.join(tmp, "ini_pip_cache")
            os.makedirs(longpath(custom_pip), exist_ok=True)
            fakes.write_file(os.path.join(custom_pip, "wheel.whl"), "wheel content")

            pip_ini = os.path.join(places.appdata, "pip", "pip.ini")
            fakes.write_file(pip_ini, f"[global]\ncache-dir = {custom_pip}\n")

            caches = find_caches(places, environ={"PATH": ""})
            pip_caches = [c for c in caches if c.tool == "pip"]
            self.assertEqual(len(pip_caches), 1)
            self.assertEqual(norm_key(pip_caches[0].path), norm_key(custom_pip))

    def test_pip_ini_env_var_interpolation(self):
        with fakes.tmpdir() as tmp:
            appdata = os.path.join(tmp, "appdata")
            target_cache = os.path.join(tmp, "var_pipcache")
            os.makedirs(longpath(target_cache), exist_ok=True)
            pip_ini = os.path.join(appdata, "pip", "pip.ini")
            fakes.write_file(pip_ini, "[global]\ncache-dir = %MY_TEST_CACHE%\\var_pipcache\n")

            with mock.patch.dict(os.environ, {"MY_TEST_CACHE": tmp}):
                res = _pip_ini_cache_dir(appdata)
                self.assertIsNotNone(res)
                self.assertEqual(norm_key(res), norm_key(target_cache))

    def test_poetry_cache_size_and_clean_excludes_virtualenvs(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            poetry_cache = os.path.join(places.localappdata, "pypoetry", "Cache")
            os.makedirs(longpath(poetry_cache), exist_ok=True)

            # Regular cache files (500 bytes)
            regular_file = os.path.join(poetry_cache, "artifacts", "pkg.whl")
            os.makedirs(longpath(os.path.dirname(regular_file)), exist_ok=True)
            with open(longpath(regular_file), "wb") as f:
                f.write(b"X" * 500)

            # Virtualenvs subdirectory (3000 bytes)
            venv_dir = os.path.join(poetry_cache, "virtualenvs", "fake-project-venv")
            fakes.make_venv(os.path.dirname(venv_dir), os.path.basename(venv_dir))
            big_file = os.path.join(venv_dir, "big.bin")
            with open(longpath(big_file), "wb") as f:
                f.write(b"Y" * 3000)

            # 1. Size calculation must exclude virtualenvs
            size, _ = measure_cache(poetry_cache, tool="poetry")
            self.assertEqual(size, 500)

            # 2. find_caches returns size 500
            caches = find_caches(places, environ={"PATH": ""})
            p_cache = next(c for c in caches if c.tool == "poetry")
            self.assertEqual(p_cache.size, 500)

            # 3. Safe delete must delete artifacts but leave virtualenvs completely intact
            lines: list[str] = []
            rc = safe_delete_cache_contents(poetry_cache, tool="poetry", on_line=lines.append)
            self.assertEqual(rc, 0)

            # Artifacts are gone
            self.assertFalse(os.path.exists(longpath(regular_file)))
            # virtualenvs and its fake env are intact
            self.assertTrue(os.path.isdir(longpath(venv_dir)))

    def test_known_envs_extensions(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. poetry config.toml virtualenvs.path
            custom_poetry_venvs = os.path.join(tmp, "custom_poetry_venvs")
            fakes.make_venv(custom_poetry_venvs, "poetry_env_1")
            poetry_cfg = os.path.join(places.appdata, "pypoetry", "config.toml")
            # Write single-quoted literal string for TOML path
            fakes.write_file(poetry_cfg, f"[virtualenvs]\npath = '{custom_poetry_venvs}'\n")

            # 2. POETRY_VIRTUALENVS_PATH
            custom_poetry_env2 = os.path.join(tmp, "custom_poetry_env2")
            fakes.make_venv(custom_poetry_env2, "poetry_env_2")

            # 3. WORKON_HOME
            custom_workon = os.path.join(tmp, "custom_workon")
            fakes.make_venv(custom_workon, "pipenv_env_1")

            # 4. UV_TOOL_DIR
            custom_uv_tools = os.path.join(tmp, "custom_uv_tools")
            fakes.make_venv(custom_uv_tools, "uv_tool_env")

            env = {
                "POETRY_VIRTUALENVS_PATH": custom_poetry_env2,
                "WORKON_HOME": custom_workon,
                "UV_TOOL_DIR": custom_uv_tools,
            }

            known = known_env_paths(places, env)
            norm_known = [norm_key(p) for p in known]

            self.assertIn(norm_key(os.path.join(custom_poetry_venvs, "poetry_env_1")), norm_known)
            self.assertIn(norm_key(os.path.join(custom_poetry_env2, "poetry_env_2")), norm_known)
            self.assertIn(norm_key(os.path.join(custom_workon, "pipenv_env_1")), norm_known)
            self.assertIn(norm_key(os.path.join(custom_uv_tools, "uv_tool_env")), norm_known)

    def test_known_envs_default_workon_home_mutation_m14(self):
        """Mutation test for M14: ensure default ~/.virtualenvs is checked when WORKON_HOME is unset."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            default_workon = os.path.join(places.home, ".virtualenvs")
            fakes.make_venv(default_workon, "default_pipenv_env")

            known = known_env_paths(places, environ={})
            norm_known = [norm_key(p) for p in known]
            self.assertIn(norm_key(os.path.join(default_workon, "default_pipenv_env")), norm_known)

    def test_uv_managed_pythons_locked(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Default uv python install dir (%APPDATA%\uv\python\*)
            default_uv_py = os.path.join(places.appdata, "uv", "python")
            p1 = fakes.make_python_install(default_uv_py, "cpython-3.12.0-windows-x86_64", version="3.12.0")

            # Custom UV_PYTHON_INSTALL_DIR
            custom_uv_py = os.path.join(tmp, "custom_uv_python")
            p2 = fakes.make_python_install(custom_uv_py, "cpython-3.13.0-windows-x86_64", version="3.13.0")

            installs_default = python_install_paths(places, [], environ={})
            norm_default = [norm_key(p) for p in installs_default]
            self.assertIn(norm_key(p1), norm_default)

            installs_custom = python_install_paths(places, [], environ={"UV_PYTHON_INSTALL_DIR": custom_uv_py})
            norm_custom = [norm_key(p) for p in installs_custom]
            self.assertIn(norm_key(p2), norm_custom)

            # Must probe as Kind.PYTHON which is locked
            env_obj1 = python_install(p1)
            self.assertIsNotNone(env_obj1)
            self.assertEqual(env_obj1.kind, Kind.PYTHON)
            self.assertTrue(env_obj1.locked)
            self.assertIn(Kind.PYTHON, LOCKED_KINDS)

    def test_clean_with_tool_on_path(self):
        fake_cache = Cache(
            tool="uv",
            path=r"C:\fake\uv\cache",
            size=1024,
            last_modified=time.time(),
            clean_method="uv cache clean",
        )

        with mock.patch("venvdiscover.caches.check_cache_safety"):
            with mock.patch("shutil.which", return_value=r"C:\tools\uv.exe"):
                with mock.patch("venvdiscover.caches.run_command_streaming", return_value=0) as mock_run:
                    lines: list[str] = []
                    ec = clean_cache(fake_cache, on_line=lines.append)
                    self.assertEqual(ec, 0)
                    mock_run.assert_called_once()
                    cmd = mock_run.call_args[0][0]
                    self.assertEqual(cmd, [r"C:\tools\uv.exe", "cache", "clean"])

            # Pip
            fake_pip = Cache("pip", r"C:\fake\pip\cache", 100, 0.0, "pip cache purge")
            with mock.patch("shutil.which", return_value=r"C:\tools\pip.exe"):
                with mock.patch("venvdiscover.caches.run_command_streaming", return_value=0) as mock_run:
                    clean_cache(fake_pip, on_line=lines.append)
                    cmd = mock_run.call_args[0][0]
                    self.assertEqual(cmd, [r"C:\tools\pip.exe", "cache", "purge"])

            # Pipenv
            fake_pipenv = Cache("pipenv", r"C:\fake\pipenv\cache", 100, 0.0, "pipenv --clear")
            with mock.patch("shutil.which", return_value=r"C:\tools\pipenv.exe"):
                with mock.patch("venvdiscover.caches.run_command_streaming", return_value=0) as mock_run:
                    clean_cache(fake_pipenv, on_line=lines.append)
                    cmd = mock_run.call_args[0][0]
                    self.assertEqual(cmd, [r"C:\tools\pipenv.exe", "--clear"])

            # Poetry
            fake_poetry = Cache("poetry", r"C:\fake\poetry\cache", 100, 0.0, "poetry cache clear")
            with mock.patch("shutil.which", return_value=r"C:\tools\poetry.exe"):
                mock_proc = mock.Mock()
                mock_proc.stdout = "pypi\ncustom-repo\n"
                with mock.patch("subprocess.run", return_value=mock_proc):
                    with mock.patch("venvdiscover.caches.run_command_streaming", return_value=0) as mock_run:
                        clean_cache(fake_poetry, on_line=lines.append)
                        self.assertEqual(mock_run.call_count, 2)
                        calls = [c[0][0] for c in mock_run.call_args_list]
                        self.assertIn([r"C:\tools\poetry.exe", "cache", "clear", "--all", "-n", "pypi"], calls)
                        self.assertIn([r"C:\tools\poetry.exe", "cache", "clear", "--all", "-n", "custom-repo"], calls)

    def test_clean_fallback_without_tool(self):
        with fakes.tmpdir() as tmp:
            cache_dir = os.path.join(tmp, "valid_uv_cache")
            os.makedirs(longpath(os.path.join(cache_dir, "wheels-v5")), exist_ok=True)
            fakes.write_file(os.path.join(cache_dir, "CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55")
            fakes.write_file(os.path.join(cache_dir, "wheels-v5", "pkg.whl"), "binary content")

            cache = Cache("uv", cache_dir, 100, 0.0, "safe permanent delete")
            lines: list[str] = []

            with mock.patch("shutil.which", return_value=None):
                ec = clean_cache(cache, on_line=lines.append)
                self.assertEqual(ec, 0)

            # Folder itself exists, but contents are gone
            self.assertTrue(os.path.isdir(longpath(cache_dir)))
            self.assertEqual(os.listdir(longpath(cache_dir)), [])

    def test_uv_cache_with_cached_script_env_cleans(self):
        """Bug 1: uv cache containing environments-v2/script-123/pyvenv.cfg must not be refused."""
        with fakes.tmpdir() as tmp:
            uv_cache = os.path.join(tmp, "uv_with_cached_env")
            os.makedirs(longpath(os.path.join(uv_cache, "wheels-v5")), exist_ok=True)
            fakes.write_file(os.path.join(uv_cache, "CACHEDIR.TAG"), "tag")
            fakes.write_file(os.path.join(uv_cache, "wheels-v5", "pkg.whl"), "wheel")
            fakes.make_venv(os.path.join(uv_cache, "environments-v2"), "script-abc123")

            cache = Cache("uv", uv_cache, 2048, time.time(), "uv cache clean")

            # 1. With tool on PATH: cleans successfully
            with mock.patch("shutil.which", return_value=r"C:\tools\uv.exe"):
                with mock.patch("venvdiscover.caches.run_command_streaming", return_value=0) as mock_run:
                    lines: list[str] = []
                    ec = clean_cache(cache, on_line=lines.append)
                    self.assertEqual(ec, 0)
                    mock_run.assert_called_once()

            # 2. In fallback: deletes contents successfully
            with mock.patch("shutil.which", return_value=None):
                lines = []
                ec = clean_cache(cache, on_line=lines.append)
                self.assertEqual(ec, 0)
                self.assertEqual(os.listdir(longpath(uv_cache)), [])

    def test_uv_cache_with_toplevel_env_refused(self):
        """Top level or arbitrary envs in uv cache must still be refused."""
        with fakes.tmpdir() as tmp:
            uv_cache = os.path.join(tmp, "bad_uv_cache")
            os.makedirs(longpath(uv_cache), exist_ok=True)
            fakes.write_file(os.path.join(uv_cache, "CACHEDIR.TAG"), "tag")
            fakes.make_venv(uv_cache, "my_custom_env")

            with self.assertRaises(ValueError) as ctx:
                check_cache_safety(uv_cache, tool="uv")
            self.assertIn("holds an env", str(ctx.exception))

    def test_clean_cache_refused_mutation_m1(self):
        """Mutation test for M1: verify clean_cache calls check_cache_safety and refuses."""
        with fakes.tmpdir() as tmp:
            bad_cache_dir = os.path.join(tmp, "bad_cache")
            os.makedirs(longpath(bad_cache_dir), exist_ok=True)
            fakes.make_venv(bad_cache_dir, "nested_env")
            fakes.write_file(os.path.join(bad_cache_dir, "file.bin"), "data")

            cache = Cache("pip", bad_cache_dir, 500, time.time(), "pip cache purge")

            # 1. With tool on PATH: must raise ValueError and not call run_command_streaming
            with mock.patch("shutil.which", return_value=r"C:\tools\pip.exe"):
                with mock.patch("venvdiscover.caches.run_command_streaming") as mock_run:
                    with self.assertRaises(ValueError):
                        clean_cache(cache, on_line=lambda s: None)
                    mock_run.assert_not_called()

            # 2. In fallback: must raise ValueError and not delete anything
            with mock.patch("shutil.which", return_value=None):
                with self.assertRaises(ValueError):
                    clean_cache(cache, on_line=lambda s: None)
                self.assertTrue(os.path.exists(longpath(os.path.join(bad_cache_dir, "file.bin"))))

    def test_fallback_refuses_arbitrary_folder_allowlist(self):
        """Bug 2: fallback permanent delete must refuse folders with unknown entries."""
        with fakes.tmpdir() as tmp:
            docs = os.path.join(tmp, "Documents")
            os.makedirs(longpath(os.path.join(docs, "photos")), exist_ok=True)
            fakes.write_file(os.path.join(docs, "thesis.docx"), "my thesis")
            fakes.write_file(os.path.join(docs, "photos", "holiday.jpg"), "photo")

            cache = Cache("pip", docs, 500, time.time(), "safe permanent delete")
            lines: list[str] = []
            with mock.patch("shutil.which", return_value=None):
                ec = clean_cache(cache, on_line=lines.append)

            self.assertEqual(ec, 1)
            self.assertTrue(any("unrecognized entry" in l for l in lines))
            # Files must remain untouched
            self.assertTrue(os.path.exists(longpath(os.path.join(docs, "thesis.docx"))))
            self.assertTrue(os.path.exists(longpath(os.path.join(docs, "photos", "holiday.jpg"))))

    def test_fallback_locked_file_returns_nonzero(self):
        """Bug 5: fallback delete must return non-zero when a file cannot be removed."""
        with fakes.tmpdir() as tmp:
            pip_cache = os.path.join(tmp, "pip_cache")
            os.makedirs(longpath(os.path.join(pip_cache, "wheels")), exist_ok=True)
            busy_file = os.path.join(pip_cache, "wheels", "busy.whl")
            fakes.write_file(busy_file, "busy")

            lines: list[str] = []
            with open(longpath(busy_file), "rb"):  # open handle prevents deletion on Windows
                ec = safe_delete_cache_contents(pip_cache, "pip", lines.append)

            self.assertNotEqual(ec, 0)
            self.assertTrue(os.path.exists(longpath(busy_file)))
            self.assertTrue(any("incomplete" in l.lower() or "could not" in l.lower() for l in lines))

    def test_refuse_cache_holding_or_inside_env(self):
        with fakes.tmpdir() as tmp:
            # 1. Cache is inside an env
            env_dir = os.path.join(tmp, "my_env")
            fakes.make_venv(tmp, "my_env")
            inside_cache = os.path.join(env_dir, "sub", "cache")
            os.makedirs(longpath(inside_cache), exist_ok=True)

            with self.assertRaises(ValueError) as ctx:
                check_cache_safety(inside_cache)
            self.assertIn("inside an env", str(ctx.exception))

            # 2. Cache directly holds an env (has pyvenv.cfg)
            direct_env = os.path.join(tmp, "env_cache")
            fakes.make_venv(tmp, "env_cache")
            with self.assertRaises(ValueError) as ctx:
                check_cache_safety(direct_env)
            self.assertIn("holds an env", str(ctx.exception))

            # 3. Cache directly holds a Python install
            py_install = fakes.make_python_install(tmp, "fake_py_install")
            with self.assertRaises(ValueError) as ctx:
                check_cache_safety(py_install)
            self.assertIn("holds a Python install", str(ctx.exception))

            # 4. Cache contains an env in a subfolder
            parent_cache = os.path.join(tmp, "bad_cache")
            os.makedirs(longpath(parent_cache), exist_ok=True)
            fakes.make_venv(parent_cache, "nested_env")
            with self.assertRaises(ValueError) as ctx:
                check_cache_safety(parent_cache)
            self.assertIn("holds an env", str(ctx.exception))

            # 5. Poetry cache with virtualenvs/ subfolder does NOT raise
            poetry_cache = os.path.join(tmp, "valid_poetry_cache")
            os.makedirs(longpath(poetry_cache), exist_ok=True)
            fakes.make_venv(poetry_cache, "virtualenvs")
            check_cache_safety(poetry_cache, tool="poetry")

    def test_mutation_guards(self):
        with fakes.tmpdir() as tmp:
            # Mutation check 1: virtualenvs exclusion alters size
            p_cache = os.path.join(tmp, "p_cache")
            os.makedirs(longpath(p_cache), exist_ok=True)
            fakes.write_file(os.path.join(p_cache, "item.txt"), "123")
            fakes.make_venv(p_cache, "virtualenvs")
            fakes.write_file(os.path.join(p_cache, "virtualenvs", "extra.bin"), "456789")

            sz_with_exclusion, _ = measure_cache(p_cache, tool="poetry")
            sz_without_exclusion, _ = measure_cache(p_cache, tool="")
            self.assertNotEqual(sz_with_exclusion, sz_without_exclusion)
            self.assertEqual(sz_with_exclusion, 3)

            # Mutation check 2: tool on PATH switches method
            places = fakes.fake_places(tmp)
            uv_dir = os.path.join(places.localappdata, "uv", "cache")
            os.makedirs(longpath(uv_dir), exist_ok=True)
            fakes.write_file(os.path.join(uv_dir, "x.txt"), "abc")

            with mock.patch("shutil.which", return_value=r"C:\bin\uv.exe"):
                caches_on_path = find_caches(places, environ={"PATH": r"C:\bin"})
                self.assertEqual(caches_on_path[0].clean_method, "uv cache clean")

            with mock.patch("shutil.which", return_value=None):
                caches_off_path = find_caches(places, environ={"PATH": ""})
                self.assertEqual(caches_off_path[0].clean_method, "safe permanent delete")

    def test_caches_dialog_ui(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            pip_dir = os.path.join(places.localappdata, "pip", "Cache")
            os.makedirs(longpath(pip_dir), exist_ok=True)
            fakes.write_file(os.path.join(pip_dir, "test.whl"), "some wheel data")

            app = tk_support.create_test_app(places, mapped=True)
            try:
                dialog = CachesDialog(app)
                pump(app, timeout=3.0, condition=lambda: len(dialog.tree.get_children()) > 0)

                children = dialog.tree.get_children()
                self.assertGreater(len(children), 0)

                dialog.tree.selection_set(children[0])
                dialog._on_tree_select(None)
                self.assertEqual(str(dialog.btn_clean.cget("state")), "normal")

                with mock.patch("tkinter.messagebox.askyesno", return_value=True):
                    with mock.patch("venvdiscover.ui.caches_dialog.clean_cache", return_value=0) as mock_clean:
                        dialog._on_clean()
                        pump(app, timeout=1.0)
                        mock_clean.assert_called_once()

                dialog.destroy()
            finally:
                app.destroy()

    def test_caches_dialog_early_destroy_does_not_freeze_app(self):
        """Bug 3: destroying dialog before loading finishes must not break App event loop."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            pip_dir = os.path.join(places.localappdata, "pip", "Cache")
            os.makedirs(longpath(pip_dir), exist_ok=True)
            fakes.write_file(os.path.join(pip_dir, "test.whl"), "wheel")

            app = tk_support.create_test_app(places, mapped=False)
            try:
                marker: list[str] = []
                app.events.put(UIAction(lambda: marker.append("before")))
                pump(app, timeout=0.5, condition=lambda: "before" in marker)
                self.assertIn("before", marker)

                with mock.patch("venvdiscover.ui.caches_dialog.find_caches", side_effect=lambda *a, **k: (time.sleep(0.3), [])[1]):
                    dialog = CachesDialog(app)
                    dialog.destroy()

                pump(app, timeout=0.8)

                app.events.put(UIAction(lambda: marker.append("after")))
                pump(app, timeout=0.5, condition=lambda: "after" in marker)
                self.assertIn("after", marker)
            finally:
                app.destroy()


    def test_failing_uiaction_does_not_break_queue_or_leak(self):
        """A failing UIAction must log debug info, must not leak to Tk's report_callback_exception,
        and must not prevent subsequent actions from executing (kills 'logger undefined' and the
        mutant where both handle_event's and the drain loop's exception handlers are removed)."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = tk_support.create_test_app(places, mapped=False)
            try:
                callback_exceptions: list[tuple] = []
                orig_report = app.report_callback_exception
                app.report_callback_exception = lambda exc, val, tb: callback_exceptions.append((exc, val, tb))

                marker: list[str] = []
                def bad_action():
                    raise RuntimeError("failing action intended for test")

                app.events.put(UIAction(bad_action))
                app.events.put(UIAction(lambda: marker.append("after_fail")))

                pump(app, timeout=1.0, condition=lambda: "after_fail" in marker)

                self.assertIn("after_fail", marker)
                self.assertEqual(callback_exceptions, [], f"Failing UIAction leaked to report_callback_exception: {callback_exceptions}")
            finally:
                app.destroy()

    def test_uv_fallback_without_cachedir_tag_refused(self):
        """Mutation U3: uv cache missing CACHEDIR.TAG must be refused by fallback and left untouched."""
        with fakes.tmpdir() as tmp:
            uv_dir = os.path.join(tmp, "uv_cache")
            wheels_dir = os.path.join(uv_dir, "wheels-v7")
            os.makedirs(longpath(wheels_dir), exist_ok=True)
            wheel_file = os.path.join(wheels_dir, "sample.whl")
            fakes.write_file(wheel_file, "binary")

            # uv tool is not on PATH
            cache = Cache("uv", uv_dir, 6, time.time(), "safe permanent delete")
            lines: list[str] = []
            ec = clean_cache(cache, on_line=lines.append, environ={"PATH": ""})
            self.assertNotEqual(ec, 0)
            self.assertTrue(any("CACHEDIR.TAG" in l for l in lines), f"Lines: {lines}")
            # Cache files must remain untouched
            self.assertTrue(os.path.isfile(longpath(wheel_file)))

    def test_clean_fallback_real_uv_layout(self):
        """Fallback cleans a real uv cache layout (crates/uv-cache/src/lib.rs: .gitignore, CACHEDIR.TAG, .lock)."""
        with fakes.tmpdir() as tmp:
            uv_dir = os.path.join(tmp, "uv_real_cache")
            os.makedirs(longpath(uv_dir), exist_ok=True)
            fakes.write_file(os.path.join(uv_dir, "CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n")
            fakes.write_file(os.path.join(uv_dir, ".gitignore"), "*\n")
            fakes.write_file(os.path.join(uv_dir, ".lock"), "")

            wheels_dir = os.path.join(uv_dir, "wheels-v7")
            os.makedirs(longpath(wheels_dir), exist_ok=True)
            fakes.write_file(os.path.join(wheels_dir, "pkg-1.0-py3-none-any.whl"), "whl content")

            sdists_dir = os.path.join(uv_dir, "sdists-v10")
            os.makedirs(longpath(sdists_dir), exist_ok=True)
            fakes.write_file(os.path.join(sdists_dir, ".gitignore"), "*\n")

            script_env = os.path.join(uv_dir, "environments-v2", "script-abc123")
            os.makedirs(longpath(script_env), exist_ok=True)
            fakes.write_file(os.path.join(script_env, "pyvenv.cfg"), "home = C:\\fake\n")

            cache = Cache("uv", uv_dir, 100, time.time(), "safe permanent delete")
            lines: list[str] = []
            ec = clean_cache(cache, on_line=lines.append, environ={"PATH": ""})
            self.assertEqual(ec, 0, f"Clean failed: {lines}")
            # Cache folder exists, but contents cleaned
            remaining = os.listdir(longpath(uv_dir))
            self.assertEqual(remaining, [])

    def test_clean_fallback_real_poetry_layout(self):
        """Fallback cleans real poetry cache layout (src/poetry/config/config.py) while preserving virtualenvs."""
        with fakes.tmpdir() as tmp:
            poetry_dir = os.path.join(tmp, "poetry_real_cache")
            repo_cache = os.path.join(poetry_dir, "cache", "repositories", "PyPI")
            artifacts_cache = os.path.join(poetry_dir, "artifacts", "wheels")
            venv_dir = os.path.join(poetry_dir, "virtualenvs", "proj-abc-py3.11")

            os.makedirs(longpath(repo_cache), exist_ok=True)
            os.makedirs(longpath(artifacts_cache), exist_ok=True)
            os.makedirs(longpath(venv_dir), exist_ok=True)

            fakes.write_file(os.path.join(poetry_dir, "CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n")
            fakes.write_file(os.path.join(repo_cache, "metadata.json"), "{}")
            fakes.write_file(os.path.join(artifacts_cache, "sample.whl"), "whl")
            fakes.write_file(os.path.join(venv_dir, "pyvenv.cfg"), "home = C:\\fake\n")

            cache = Cache("poetry", poetry_dir, 100, time.time(), "safe permanent delete")
            lines: list[str] = []
            ec = clean_cache(cache, on_line=lines.append, environ={"PATH": ""})
            self.assertEqual(ec, 0, f"Clean failed: {lines}")
            # virtualenvs must be preserved
            self.assertTrue(os.path.isfile(os.path.join(longpath(venv_dir), "pyvenv.cfg")))
            # cache and artifacts must be removed
            self.assertFalse(os.path.exists(os.path.join(longpath(poetry_dir), "cache")))
            self.assertFalse(os.path.exists(os.path.join(longpath(poetry_dir), "artifacts")))

    def test_uv_cache_long_path_safety(self):
        """Long cache path (>= 240 chars) skips uv buckets properly without raising."""
        with fakes.tmpdir() as tmp:
            parts = ["deep_segment_" + str(i).zfill(3) for i in range(16)]
            long_cache = os.path.join(tmp, *parts)
            env_dir = os.path.join(long_cache, "environments-v2", "script-xyz")
            os.makedirs(longpath(env_dir), exist_ok=True)
            fakes.write_file(os.path.join(env_dir, "pyvenv.cfg"), "home = C:\\fake\n")

            lp = longpath(long_cache)
            self.assertGreaterEqual(len(lp), 240)
            self.assertTrue(lp.startswith("\\\\?\\"), f"Expected longpath prefix, got {lp}")
            # Must not raise ValueError because environments-v2 bucket is correctly skipped
            check_cache_safety(long_cache, tool="uv")

    def test_cli_json_caches(self):
        """Test that cli --json --caches lists caches, and without --caches omits the key (read-only).
        Hermetic: passes explicit environ, ignoring host or mock PIP_CACHE_DIR / UV_CACHE_DIR."""
        import io
        import json
        from unittest import mock
        from venvdiscover import cli

        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            scan_dir = os.path.join(tmp, "scan_root")
            os.makedirs(scan_dir, exist_ok=True)
            pip_dir = os.path.join(places.localappdata, "pip", "Cache")
            wheels_dir = os.path.join(pip_dir, "wheels")
            os.makedirs(longpath(wheels_dir), exist_ok=True)
            fakes.write_file(os.path.join(wheels_dir, "pkg.whl"), "sample data")

            # Stand-in dirs under tmp set in os.environ to prove cli.main does not leak to os.environ
            standin_dir = os.path.join(tmp, "standin_env")
            standin_pip = os.path.join(standin_dir, "pip")
            standin_uv = os.path.join(standin_dir, "uv")
            os.makedirs(standin_pip, exist_ok=True)
            os.makedirs(standin_uv, exist_ok=True)
            fakes.write_file(os.path.join(standin_pip, "standin.whl"), "standin data")
            fakes.write_file(os.path.join(standin_uv, "CACHEDIR.TAG"), "Signature: 8a477f07")

            fake_env = {
                "LOCALAPPDATA": places.localappdata,
                "APPDATA": places.appdata,
                "USERPROFILE": places.home,
                "PATH": "",
            }

            scanned_paths: list[str] = []
            real_scandir = os.scandir

            def audit_scandir(path="."):
                scanned_paths.append(str(path))
                return real_scandir(path)

            with mock.patch.dict(os.environ, {"PIP_CACHE_DIR": standin_pip, "UV_CACHE_DIR": standin_uv}):
                with mock.patch("os.scandir", side_effect=audit_scandir):
                    # 1. With --json --caches
                    out = io.StringIO()
                    rc = cli.main(
                        ["--json", "--paths", scan_dir, "--caches"],
                        places=places,
                        environ=fake_env,
                        stdout=out,
                    )
                    self.assertEqual(rc, 0)
                    data = json.loads(out.getvalue())
                    self.assertIn("caches", data)
                    self.assertIsInstance(data["caches"], list)
                    pip_entry = next((c for c in data["caches"] if c.get("tool") == "pip"), None)
                    self.assertIsNotNone(pip_entry)
                    self.assertIn("path", pip_entry)
                    # Reported pip path is the fake one from places, NOT the standin from os.environ
                    self.assertEqual(pip_entry["path"], pip_dir)
                    self.assertNotEqual(pip_entry["path"], standin_pip)
                    self.assertIn("size", pip_entry)
                    self.assertGreater(pip_entry["size"], 0)

                    # Check that file still exists (read-only, never cleans)
                    self.assertTrue(os.path.isfile(os.path.join(longpath(wheels_dir), "pkg.whl")))

                    # Verify that no directory outside test tmp was scanned
                    tmp_clean = os.path.abspath(tmp).lower()
                    for p in scanned_paths:
                        p_clean = os.path.abspath(p.removeprefix("\\\\?\\")).lower()
                        self.assertTrue(
                            p_clean.startswith(tmp_clean),
                            f"Scanned path outside test tmp: {p}",
                        )
                    # And stand-in caches from os.environ were never scanned
                    self.assertFalse(any("standin_env" in p.lower() for p in scanned_paths))

                    # 2. Without --caches
                    out2 = io.StringIO()
                    rc2 = cli.main(
                        ["--json", "--paths", scan_dir],
                        places=places,
                        environ=fake_env,
                        stdout=out2,
                    )
                    self.assertEqual(rc2, 0)
                    data2 = json.loads(out2.getvalue())
                    self.assertNotIn("caches", data2)


if __name__ == "__main__":
    unittest.main()
