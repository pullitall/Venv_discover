"""Unit tests for the main Tkinter UI application."""

from __future__ import annotations

import gc
import os
import queue
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from tests import fakes
from tests.tk_support import create_test_app
from venvdiscover import store
from venvdiscover.actions import activate_command
from venvdiscover.model import Env, Kind, Package, norm_key
from venvdiscover.scanner import (
    EnvFound,
    EnvGone,
    EnvUpdated,
    Progress,
    ScanDone,
    Scanner,
    SizesDone,
)
from venvdiscover.ui.app import App, CondaCleanDialog, DrivesDialog
from venvdiscover.ui.delete_dialog import ConfirmDeleteDialog
from venvdiscover.walker import Drive


def pump(app: App, timeout: float = 10.0, condition=None) -> None:
    """Pump tkinter event loop until condition is met or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if condition and condition():
            return
        time.sleep(0.02)
    if condition and not condition():
        raise TimeoutError("Condition not met within timeout")


class DummyScanner:
    """No-op scanner for controlled event feeding in tests."""

    def __init__(self, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass


class UIMainTests(unittest.TestCase):
    def tearDown(self) -> None:
        gc.collect()

    def test_rows_appear(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            base = fakes.make_conda_root(tmp, "miniconda_base")
            v1 = fakes.make_venv(tmp, "my_venv", packages={"numpy": "1.24.0"})

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: Scanner(environ={}, registry_paths=lambda: [], **kw),
            )
            app.withdraw()
            try:
                # Pump until ScanDone has been processed
                pump(app, timeout=10.0, condition=lambda: "Found" in app.status_var.get())

                keys = list(app.envs.keys())
                self.assertIn(norm_key(base), keys)
                self.assertIn(norm_key(v1), keys)

                # Check kind column shows '(locked)' for the conda base
                base_item = app.envs_table.tree.item(norm_key(base))
                values = base_item["values"]
                # Column 1 is kind
                self.assertEqual(values[1], "conda base (locked)")

                # Normal venv is unlocked
                v1_item = app.envs_table.tree.item(norm_key(v1))
                self.assertEqual(v1_item["values"][1], "venv")
            finally:
                app.destroy()

    def test_search_mode(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            fakes.make_venv(tmp, "v_numpy", packages={"numpy": "1.24.0", "requests": "2.28.0"})
            fakes.make_venv(tmp, "v_scipy", packages={"scipy": "1.10.0"})

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: Scanner(environ={}, registry_paths=lambda: [], **kw),
            )
            app.withdraw()
            try:
                pump(app, timeout=10.0, condition=lambda: "Found" in app.status_var.get())

                # PackagesPanel is visible by default (packed in right container)
                self.assertIn(app.packages_panel, app.right_container.pack_slaves())
                self.assertNotIn(app.search_results, app.right_container.pack_slaves())

                # Type 'numpy' into search entry
                app.search_var.set("numpy")
                # Wait > 150 ms debounce
                pump(
                    app,
                    timeout=1.0,
                    condition=lambda: app.search_results in app.right_container.pack_slaves(),
                )

                self.assertIn(app.search_results, app.right_container.pack_slaves())
                self.assertNotIn(app.packages_panel, app.right_container.pack_slaves())

                # Check search results rows
                results = app.search_results.tree.get_children()
                self.assertEqual(len(results), 1)
                vals = app.search_results.tree.item(results[0], "values")
                self.assertEqual(vals[0], "v_numpy")
                self.assertEqual(vals[1], "numpy")
                self.assertEqual(vals[2], "1.24.0")

                # Clearing the search box brings PackagesPanel back
                app.search_var.set("")
                pump(
                    app,
                    timeout=1.0,
                    condition=lambda: app.packages_panel in app.right_container.pack_slaves(),
                )

                self.assertIn(app.packages_panel, app.right_container.pack_slaves())
                self.assertNotIn(app.search_results, app.right_container.pack_slaves())
            finally:
                app.destroy()

    def test_cache_written(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "cached_venv")

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: Scanner(environ={}, registry_paths=lambda: [], **kw),
            )
            app.withdraw()
            try:
                pump(app, timeout=10.0, condition=lambda: "Found" in app.status_var.get())

                self.assertTrue(os.path.isfile(places.cache_path))
                loaded = store.load_cache(places.cache_path)
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].key, norm_key(v))
            finally:
                app.destroy()

    def test_copy_activate(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "my_venv")

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: Scanner(environ={}, registry_paths=lambda: [], **kw),
            )
            app.withdraw()
            try:
                pump(app, timeout=10.0, condition=lambda: "Found" in app.status_var.get())

                v_key = norm_key(v)
                app.envs_table.tree.selection_set(v_key)
                app._update_action_states()

                self.assertEqual(str(app.btn_copy["state"]), "normal")

                clipboard_contents: list[str] = []
                with patch.object(app, "clipboard_clear", side_effect=clipboard_contents.clear), \
                     patch.object(app, "clipboard_append", side_effect=clipboard_contents.append):
                    app.btn_copy.invoke()
                    env = app.envs[v_key]
                    self.assertEqual(clipboard_contents, [activate_command(env)])
            finally:
                app.destroy()

    def test_drive_settings(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            fake_drives = [
                Drive("C:\\", "NTFS", "OS"),
                Drive("D:\\", "NTFS", "Data"),
            ]

            with patch("venvdiscover.ui.app.local_drives", return_value=fake_drives):
                app = create_test_app(
                    places,
                    drive_roots=[tmp],
                    scanner_factory=lambda **kw: DummyScanner(),
                )
                app.withdraw()
                try:
                    dlg = DrivesDialog(app)
                    dlg.withdraw()

                    # Untick first drive (C:\)
                    dlg.check_vars[0].set(False)
                    dlg.on_save()

                    s = store.load_settings(places.settings_path)
                    self.assertIsNotNone(s.drives)
                    self.assertNotIn("C:\\", s.drives)
                    self.assertIn("D:\\", s.drives)
                finally:
                    app.destroy()

    def test_sort_temp_always_last_both_ways(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            e_small = Env(os.path.join(tmp, "small"), Kind.VENV, "small", size=100)
            e_large = Env(os.path.join(tmp, "large"), Kind.VENV, "large", size=5000)
            e_temp = Env(os.path.join(tmp, "temp_env"), Kind.TEMP_VENV, "temp_env", size=99999)

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                for env in (e_small, e_large, e_temp):
                    app.envs[env.key] = env
                    app.envs_table.upsert(env)

                # 1. Click Size header once (ascending sort)
                app.envs_table._on_heading_click("size")
                children_asc = list(app.envs_table.tree.get_children())
                self.assertEqual(children_asc, [e_small.key, e_large.key, e_temp.key])

                # 2. Click Size header second time (descending sort)
                app.envs_table._on_heading_click("size")
                children_desc = list(app.envs_table.tree.get_children())
                self.assertEqual(children_desc, [e_large.key, e_small.key, e_temp.key])

                # Temp row has "temp" tag, non-temp row does not
                self.assertIn("temp", app.envs_table.tree.item(e_temp.key, "tags"))
                self.assertNotIn("temp", app.envs_table.tree.item(e_small.key, "tags"))
            finally:
                app.destroy()

    def test_temp_last_without_sort(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            e_temp = Env(os.path.join(tmp, "temp"), Kind.TEMP_VENV, "temp")
            e_venv = Env(os.path.join(tmp, "venv"), Kind.VENV, "venv")

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                # EnvFound(temp) first, then EnvFound(venv)
                app.events.put(EnvFound(e_temp))
                app.events.put(EnvFound(e_venv))

                pump(app, timeout=5.0, condition=lambda: len(app.envs_table.tree.get_children()) == 2)
                children = list(app.envs_table.tree.get_children())
                self.assertEqual(children, [e_venv.key, e_temp.key])
            finally:
                app.destroy()

    def test_sort_follows_updates(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            e1 = Env(os.path.join(tmp, "e1"), Kind.VENV, "e1", size=100)
            e2 = Env(os.path.join(tmp, "e2"), Kind.VENV, "e2", size=200)

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.envs[e1.key] = e1
                app.envs_table.upsert(e1)
                app.envs[e2.key] = e2
                app.envs_table.upsert(e2)

                # Sort by size descending: first click ascending, second click descending
                app.envs_table._on_heading_click("size")
                app.envs_table._on_heading_click("size")
                self.assertEqual(list(app.envs_table.tree.get_children()), [e2.key, e1.key])

                # Give smallest env the largest size in an EnvUpdated event
                e1_updated = Env(os.path.join(tmp, "e1"), Kind.VENV, "e1", size=500)
                app.events.put(EnvUpdated(e1_updated))

                pump(app, timeout=5.0, condition=lambda: list(app.envs_table.tree.get_children()) == [e1.key, e2.key])
                self.assertEqual(list(app.envs_table.tree.get_children()), [e1.key, e2.key])
            finally:
                app.destroy()

    def test_search_refreshes(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_numpy = Env(
                os.path.join(tmp, "v_numpy"),
                Kind.VENV,
                "v_numpy",
                packages=[Package("numpy", "1.24.0", "pip")],
            )

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                # Type 'numpy' into search entry with no envs loaded
                app.search_var.set("numpy")
                pump(app, timeout=1.0, condition=lambda: app.search_results in app.right_container.pack_slaves())
                self.assertEqual(len(app.search_results.tree.get_children()), 0)

                # EnvFound arrives while search box is non-empty
                app.events.put(EnvFound(v_numpy))

                # After debounce (> 150 ms), search results must refresh to show the new env
                pump(app, timeout=2.0, condition=lambda: len(app.search_results.tree.get_children()) == 1)
                self.assertEqual(len(app.search_results.tree.get_children()), 1)
            finally:
                app.destroy()

    def test_search_cap(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            pkgs = [Package(f"pkg{i}", "1.0", "pip") for i in range(1500)]
            env = Env(os.path.join(tmp, "big_env"), Kind.VENV, "big_env", packages=pkgs)

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.envs[env.key] = env
                app.envs_table.upsert(env)

                app.search_var.set("pkg")
                pump(app, timeout=2.0, condition=lambda: len(app.search_results.tree.get_children()) == 1000)

                self.assertEqual(len(app.search_results.tree.get_children()), 1000)
                self.assertEqual(app.search_results.footer_label.cget("text"), "Showing first 1000 of 1500")
            finally:
                app.destroy()

    def test_cached_rows_first(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env = Env(os.path.join(tmp, "cached_env"), Kind.VENV, "cached_env")
            store.save_cache(places.cache_path, [env])

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                # Row exists straight after constructor, before any update()
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))
            finally:
                app.destroy()

    def test_close_saves_cache(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env = Env(os.path.join(tmp, "env_close"), Kind.VENV, "env_close")

            mock_scanner = DummyScanner()
            with patch.object(mock_scanner, "stop") as mock_stop:
                app = create_test_app(
                    places,
                    drive_roots=[],
                    scanner_factory=lambda **kw: mock_scanner,
                )
                app.withdraw()
                app.envs[env.key] = env

                app._on_close()

                mock_stop.assert_called_once()
                loaded = store.load_cache(places.cache_path)
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].key, env.key)

    def test_action_states(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env_py = Env(os.path.join(tmp, "py"), Kind.PYTHON, "py")
            env_base = Env(os.path.join(tmp, "base"), Kind.CONDA_BASE, "base")
            env_venv = Env(os.path.join(tmp, "venv"), Kind.VENV, "venv")

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                for env in (env_py, env_base, env_venv):
                    app.envs[env.key] = env
                    app.envs_table.upsert(env)

                # 1. Select only Python install: Copy disabled (cmd is ""), Delete disabled (locked)
                app.envs_table.tree.selection_set(env_py.key)
                app._update_action_states()
                self.assertEqual(str(app.btn_copy["state"]), "disabled")
                self.assertEqual(str(app.btn_delete["state"]), "disabled")

                # 2. Select only Conda base: Delete disabled (locked)
                app.envs_table.tree.selection_set(env_base.key)
                app._update_action_states()
                self.assertEqual(str(app.btn_delete["state"]), "disabled")

                # 3. Select both base and venv: Delete enabled (not all locked)
                app.envs_table.tree.selection_set([env_base.key, env_venv.key])
                app._update_action_states()
                self.assertEqual(str(app.btn_delete["state"]), "normal")
            finally:
                app.destroy()

    def test_drives_defaults(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            fake_drives = [
                Drive("C:\\", "NTFS", "OS"),
                Drive("D:\\", "ReFS", "Data"),
                Drive("E:\\", "FAT32", "USB"),
            ]

            with patch("venvdiscover.ui.app.local_drives", return_value=fake_drives):
                app = create_test_app(
                    places,
                    drive_roots=[],
                    scanner_factory=lambda **kw: DummyScanner(),
                )
                app.withdraw()
                try:
                    # 1. With no settings: NTFS and ReFS are ticked, FAT32 is not
                    dlg1 = DrivesDialog(app)
                    dlg1.withdraw()
                    self.assertTrue(dlg1.check_vars[0].get())  # C: NTFS
                    self.assertTrue(dlg1.check_vars[1].get())  # D: ReFS
                    self.assertFalse(dlg1.check_vars[2].get())  # E: FAT32
                    dlg1.destroy()

                    # 2. With saved settings: exactly the saved drives are ticked
                    store.save_settings(places.settings_path, store.Settings(drives=["E:\\"]))
                    dlg2 = DrivesDialog(app)
                    dlg2.withdraw()
                    self.assertFalse(dlg2.check_vars[0].get())  # C:
                    self.assertFalse(dlg2.check_vars[1].get())  # D:
                    self.assertTrue(dlg2.check_vars[2].get())   # E:
                    dlg2.destroy()
                finally:
                    app.destroy()

    def test_rescan_uses_saved_drives(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            store.save_settings(places.settings_path, store.Settings(drives=["X:\\"]))

            recorded_roots: list[list[str]] = []

            def recording_scanner_factory(**kw):
                recorded_roots.append(kw.get("roots", []))
                return DummyScanner()

            app = create_test_app(
                places,
                drive_roots=None,
                scanner_factory=recording_scanner_factory,
            )
            app.withdraw()
            try:
                self.assertTrue(len(recorded_roots) > 0)
                self.assertEqual(recorded_roots[-1], ["X:\\"])
            finally:
                app.destroy()

    def test_env_gone_removes_row(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env = Env(os.path.join(tmp, "env_gone"), Kind.VENV, "env_gone")

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=5.0, condition=lambda: app.envs_table.tree.exists(env.key))

                app.events.put(EnvGone(env.key))
                pump(app, timeout=5.0, condition=lambda: not app.envs_table.tree.exists(env.key))

                self.assertNotIn(env.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env.key))
            finally:
                app.destroy()

    def test_select_shows_packages(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env = Env(
                os.path.join(tmp, "flask_env"),
                Kind.VENV,
                "flask_env",
                packages=[Package("flask", "2.0.1", "pip"), Package("click", "8.0", "pip")],
            )

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.envs[env.key] = env
                app.envs_table.upsert(env)

                app.envs_table.tree.selection_set(env.key)
                app._on_table_select([env.key])

                pkg_rows = app.packages_panel.tree.get_children()
                self.assertEqual(len(pkg_rows), 2)
                row0 = app.packages_panel.tree.item(pkg_rows[0], "values")
                row1 = app.packages_panel.tree.item(pkg_rows[1], "values")
                self.assertEqual(row0[0], "flask")
                self.assertEqual(row1[0], "click")
            finally:
                app.destroy()

    def test_scan_done_sizes_done_flow(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            e1 = Env(os.path.join(tmp, "e1"), Kind.VENV, "e1", size=None)
            e2 = Env(os.path.join(tmp, "e2"), Kind.VENV, "e2", size=None)

            app = create_test_app(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                # 1. Feed initial found envs and ScanDone
                app.events.put(EnvFound(e1))
                app.events.put(EnvFound(e2))
                app.events.put(ScanDone(seconds=1.5, total=2))

                pump(app, timeout=5.0, condition=lambda: app.status_var.get() == "Found 2 envs in 1.5 s")
                self.assertEqual(app.status_var.get(), "Found 2 envs in 1.5 s")

                # Verify cache is saved on ScanDone (before late size updates arrive)
                cached_on_scandone = store.load_cache(places.cache_path)
                self.assertEqual(len(cached_on_scandone), 2)
                self.assertEqual({e.key for e in cached_on_scandone}, {e1.key, e2.key})

                # Size cells show '…'
                vals1 = app.envs_table.tree.item(e1.key, "values")
                vals2 = app.envs_table.tree.item(e2.key, "values")
                self.assertEqual(vals1[4], "…")
                self.assertEqual(vals2[4], "…")

                # 2. Progress during sizing
                app.events.put(Progress("Measuring sizes — 2 left"))
                pump(app, timeout=5.0, condition=lambda: app.status_var.get() == "Measuring sizes — 2 left")
                self.assertEqual(app.status_var.get(), "Measuring sizes — 2 left")

                # 3. Sizes finish arriving as EnvUpdated
                e1.size = 1024
                e2.size = 2048
                app.events.put(EnvUpdated(e1))
                app.events.put(EnvUpdated(e2))
                app.events.put(SizesDone())

                # On SizesDone, status returns to 'Found 2 envs in 1.5 s'
                pump(app, timeout=5.0, condition=lambda: app.status_var.get() == "Found 2 envs in 1.5 s")
                self.assertEqual(app.status_var.get(), "Found 2 envs in 1.5 s")

                # Size cells are filled with formatted sizes
                vals1_after = app.envs_table.tree.item(e1.key, "values")
                vals2_after = app.envs_table.tree.item(e2.key, "values")
                self.assertEqual(vals1_after[4], "1.0 KB")
                self.assertEqual(vals2_after[4], "2.0 KB")

                # Cache holds the sizes
                cached = store.load_cache(places.cache_path)
                self.assertEqual(len(cached), 2)
                cache_by_key = {e.key: e for e in cached}
                self.assertEqual(cache_by_key[e1.key].size, 1024)
                self.assertEqual(cache_by_key[e2.key].size, 2048)
            finally:
                app.destroy()

    def test_no_window_on_screen(self) -> None:
        """Build an App, open each dialog through app code paths; all toplevels are invisible or off-screen (<= -10000)."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            conda_root = fakes.make_conda_root(tmp, "conda_base")
            fakes.write_file(os.path.join(conda_root, "condabin", "conda.bat"), "@echo off\n")
            env_conda = Env(conda_root, Kind.CONDA_BASE, "conda_base", size=1024)
            v_path = fakes.make_venv(tmp, "test_venv")
            env_venv = Env(v_path, Kind.VENV, "test_venv", size=2048)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.envs[env_conda.key] = env_conda
                app.envs[env_venv.key] = env_venv

                def check_toplevels(expected_count: int | None = None) -> list[tk.Toplevel]:
                    toplevels = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
                    if expected_count is not None:
                        self.assertEqual(len(toplevels), expected_count)
                    for top in toplevels:
                        top.update_idletasks()
                        if top.winfo_viewable():
                            self.assertLessEqual(top.winfo_rootx(), -10000)
                            self.assertLessEqual(top.winfo_rooty(), -10000)
                    return toplevels

                # Main app window
                app.update_idletasks()
                if app.winfo_viewable():
                    self.assertLessEqual(app.winfo_rootx(), -10000)
                    self.assertLessEqual(app.winfo_rooty(), -10000)

                # 1. DrivesDialog opened through app._on_open_drives()
                with patch("venvdiscover.ui.app.store.save_settings"):
                    app._on_open_drives()
                    drives_dlgs = check_toplevels(1)
                    self.assertIsInstance(drives_dlgs[0], DrivesDialog)
                    drives_dlgs[0].destroy()
                    app.update()

                # 2. CondaCleanDialog opened through app._on_clean_conda_cache()
                with patch("tkinter.messagebox.askyesno", return_value=True), \
                     patch("threading.Thread"):
                    app._on_clean_conda_cache()
                    clean_dlgs = check_toplevels(1)
                    self.assertIsInstance(clean_dlgs[0], CondaCleanDialog)
                    clean_dlgs[0].destroy()
                    app.update()

                # 3. ConfirmDeleteDialog opened through app.request_delete()
                with patch("tkinter.messagebox.showerror"), \
                     patch("tkinter.messagebox.askyesno", return_value=True):
                    app.request_delete([env_venv])
                    pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                    del_dlg = app._active_delete_dialog
                    self.assertIsNotNone(del_dlg)
                    self.assertIsInstance(del_dlg, ConfirmDeleteDialog)
                    check_toplevels()
                    del_dlg.destroy()
                    app.update()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
