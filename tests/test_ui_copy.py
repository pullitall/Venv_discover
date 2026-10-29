"""Unit tests for right-click context menu, Copy actions, terminal submenu, and Ctrl+C."""

from __future__ import annotations

import gc
import os
import tkinter as tk
import unittest
from unittest.mock import patch

from tests import fakes, tk_support
from tests.tk_support import DummyScanner
from venvdiscover import store
from venvdiscover.model import Env, Kind, Package
from venvdiscover.scanner import Scanner
from venvdiscover.terminal import Terminal
from venvdiscover.ui.app import App


def _dummy_event() -> tk.Event:
    ev = tk.Event()
    ev.x_root = 0
    ev.y_root = 0
    return ev





class UICopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = fakes.tmpdir()
        self.tmp = self.tmp_ctx.__enter__()
        self.places = fakes.fake_places(self.tmp)

    def tearDown(self) -> None:
        try:
            self.tmp_ctx.__exit__(None, None, None)
        except Exception:
            pass
        gc.collect()

    def _create_app(self, mapped: bool = False, environ: dict[str, str] | None = None) -> App:
        app = tk_support.create_test_app(
            self.places,
            drive_roots=[self.tmp],
            scanner_factory=lambda **kw: DummyScanner(),
            mapped=mapped,
            environ=environ or {},
        )
        return app

    def test_copy_path_single_and_multi(self) -> None:
        """One env copies its path; three selected copy three lines in table order."""
        app = self._create_app(mapped=False)
        try:
            p1 = os.path.join(self.tmp, "env_a")
            p2 = os.path.join(self.tmp, "env_b")
            p3 = os.path.join(self.tmp, "env_c")
            os.makedirs(p1, exist_ok=True)
            os.makedirs(p2, exist_ok=True)
            os.makedirs(p3, exist_ok=True)

            e1 = Env(p1, Kind.VENV, "env_a")
            e2 = Env(p2, Kind.VENV, "env_b")
            e3 = Env(p3, Kind.VENV, "env_c")

            for e in (e1, e2, e3):
                app.envs[e.key] = e
                app.envs_table.upsert(e)

            # Single selection
            app.envs_table.tree.selection_set(e2.key)
            app._update_action_states()
            self.assertEqual(str(app.btn_copy_path["state"]), "normal")

            clipboard: list[str] = []
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_path()

            self.assertEqual(clipboard, [e2.path])
            self.assertEqual(app.status_var.get(), f"Copied path of {e2.name}")

            # Also invoke via button
            clipboard.clear()
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app.btn_copy_path.invoke()

            self.assertEqual(clipboard, [e2.path])

            # Multi-selection in mixed selection order (e3, e1, e2)
            # Table order should be preserved: e1, e2, e3
            app.envs_table.tree.selection_set(e3.key, e1.key, e2.key)
            app._update_action_states()
            self.assertEqual(str(app.btn_copy_path["state"]), "normal")

            table_order_paths = [
                app.envs[k].path for k in app.envs_table.tree.get_children("")
                if k in (e1.key, e2.key, e3.key)
            ]
            expected_output = "\n".join(table_order_paths)

            clipboard.clear()
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_path()

            self.assertEqual(clipboard, [expected_output])
            self.assertEqual(app.status_var.get(), "Copied 3 paths")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_copy_python_path(self) -> None:
        """Fake venv copies Scripts/python.exe; conda copies python.exe; missing interpreter is disabled."""
        app = self._create_app(mapped=False)
        try:
            # 1. Fake venv with Scripts\python.exe
            v_dir = os.path.join(self.tmp, "fake_venv")
            os.makedirs(os.path.join(v_dir, "Scripts"), exist_ok=True)
            v_exe = os.path.join(v_dir, "Scripts", "python.exe")
            with open(v_exe, "w", encoding="utf-8") as f:
                f.write("")
            env_venv = Env(v_dir, Kind.VENV, "fake_venv")

            # 2. Fake conda env with python.exe
            c_dir = os.path.join(self.tmp, "fake_conda")
            os.makedirs(c_dir, exist_ok=True)
            c_exe = os.path.join(c_dir, "python.exe")
            with open(c_exe, "w", encoding="utf-8") as f:
                f.write("")
            env_conda = Env(c_dir, Kind.CONDA, "fake_conda")

            # 3. Env with no interpreter
            n_dir = os.path.join(self.tmp, "no_interp")
            os.makedirs(n_dir, exist_ok=True)
            env_no_interp = Env(n_dir, Kind.VENV, "no_interp")

            for e in (env_venv, env_conda, env_no_interp):
                app.envs[e.key] = e
                app.envs_table.upsert(e)

            # Test venv
            app.envs_table.tree.selection_set(env_venv.key)
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "normal")

            clipboard: list[str] = []
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_python_path()

            self.assertEqual(clipboard, [v_exe])
            self.assertEqual(app.status_var.get(), f"Copied Python path of {env_venv.name}")

            # Test conda
            app.envs_table.tree.selection_set(env_conda.key)
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "normal")

            clipboard.clear()
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_python_path()

            self.assertEqual(clipboard, [c_exe])
            self.assertEqual(app.status_var.get(), f"Copied Python path of {env_conda.name}")

            # Test no interpreter
            app.envs_table.tree.selection_set(env_no_interp.key)
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "disabled")

            # Test multi-selection disabled
            app.envs_table.tree.selection_set(env_venv.key, env_conda.key)
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "disabled")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_copy_package_list(self) -> None:
        """Exact text for fake env with numpy 1.26.4, Pandas 2.2.3, attrs 23.1.0."""
        app = self._create_app(mapped=False)
        try:
            p_dir = os.path.join(self.tmp, "pkg_env")
            os.makedirs(p_dir, exist_ok=True)
            env = Env(
                p_dir,
                Kind.VENV,
                "pkg_env",
                packages=[
                    Package("numpy", "1.26.4", "pip"),
                    Package("Pandas", "2.2.3", "pip"),
                    Package("attrs", "23.1.0", "pip"),
                ],
            )
            app.envs[env.key] = env
            app.envs_table.upsert(env)

            # Env with no packages
            empty_dir = os.path.join(self.tmp, "empty_env")
            os.makedirs(empty_dir, exist_ok=True)
            empty_env = Env(empty_dir, Kind.VENV, "empty_env", packages=[])
            app.envs[empty_env.key] = empty_env
            app.envs_table.upsert(empty_env)

            # Select pkg_env
            app.envs_table.tree.selection_set(env.key)
            app._on_table_select([env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "normal")

            clipboard: list[str] = []
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_package_list()

            expected_text = "attrs==23.1.0\nnumpy==1.26.4\nPandas==2.2.3"
            self.assertEqual(clipboard, [expected_text])
            self.assertEqual(app.status_var.get(), f"Copied 3 packages of {env.name}")

            # With a filter typed in packages panel (e.g. "num"), the panel shows only 1 package,
            # but copying package list MUST copy the env's full list, sorted case-insensitively.
            app.packages_panel.filter_var.set("num")
            app.packages_panel._on_filter_changed()
            self.assertEqual(len(app.packages_panel.tree.get_children()), 1)

            clipboard.clear()
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app._on_copy_package_list()

            self.assertEqual(clipboard, [expected_text])
            self.assertEqual(app.status_var.get(), f"Copied 3 packages of {env.name}")

            # Select empty_env -> disabled
            app.envs_table.tree.selection_set(empty_env.key)
            app._on_table_select([empty_env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "disabled")

            # Multi-select -> disabled
            app.envs_table.tree.selection_set(env.key, empty_env.key)
            app._on_table_select([env.key, empty_env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "disabled")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_ctrl_c_copies_path(self) -> None:
        """event_generate('<Control-c>') on the table copies the selected path."""
        app = self._create_app(mapped=True)
        try:
            p = os.path.join(self.tmp, "ctrl_c_env")
            os.makedirs(p, exist_ok=True)
            env = Env(p, Kind.VENV, "ctrl_c_env")
            app.envs[env.key] = env
            app.envs_table.upsert(env)

            app.envs_table.tree.selection_set(env.key)
            app._update_action_states()

            clipboard: list[str] = []
            with patch.object(app, "clipboard_clear", side_effect=clipboard.clear), \
                 patch.object(app, "clipboard_append", side_effect=clipboard.append):
                app.envs_table.tree.focus_force()
                app.update()
                app.envs_table.tree.event_generate("<Control-c>")
                app.update()

            self.assertEqual(clipboard, [env.path])
            self.assertEqual(app.status_var.get(), f"Copied path of {env.name}")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_menu_states(self) -> None:
        """No selection, one env, two envs, and a locked env each enable exactly the right items."""
        app = self._create_app(mapped=False)
        try:
            # 1. Unlocked env with python.exe and activate script and packages
            u_dir = os.path.join(self.tmp, "unlocked_env")
            os.makedirs(os.path.join(u_dir, "Scripts"), exist_ok=True)
            with open(os.path.join(u_dir, "Scripts", "python.exe"), "w") as f:
                f.write("")
            with open(os.path.join(u_dir, "Scripts", "activate.bat"), "w") as f:
                f.write("")
            u_env = Env(
                u_dir,
                Kind.VENV,
                "unlocked_env",
                packages=[Package("pytest", "8.0.0", "pip")],
            )

            # 2. Second unlocked env
            u2_dir = os.path.join(self.tmp, "unlocked_env2")
            os.makedirs(os.path.join(u2_dir, "Scripts"), exist_ok=True)
            with open(os.path.join(u2_dir, "Scripts", "python.exe"), "w") as f:
                f.write("")
            u2_env = Env(u2_dir, Kind.VENV, "unlocked_env2")

            # 3. Locked env (Python install)
            l_dir = os.path.join(self.tmp, "python_locked")
            os.makedirs(l_dir, exist_ok=True)
            with open(os.path.join(l_dir, "python.exe"), "w") as f:
                f.write("")
            l_env = Env(
                l_dir,
                Kind.PYTHON,
                "python_locked",
                packages=[Package("pip", "24.0", "pip")],
            )

            for e in (u_env, u2_env, l_env):
                app.envs[e.key] = e
                app.envs_table.upsert(e)

            # Case A: No selection
            app.envs_table.tree.selection_set()
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Open folder", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Open terminal", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy path", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy activate command", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Delete…", "state"), "disabled")

            # Case B: One unlocked env
            app.envs_table.tree.selection_set(u_env.key)
            app._on_table_select([u_env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Open folder", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Open terminal", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy path", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy activate command", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Delete…", "state"), "normal")

            # Case C: Two envs
            app.envs_table.tree.selection_set(u_env.key, u2_env.key)
            app._on_table_select([u_env.key, u2_env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Open folder", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Open terminal", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy path", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy activate command", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "disabled")
            self.assertEqual(app.context_menu.entrycget("Delete…", "state"), "normal")

            # Case D: Locked env
            app.envs_table.tree.selection_set(l_env.key)
            app._on_table_select([l_env.key])
            app._show_context_menu(_dummy_event())
            self.assertEqual(app.context_menu.entrycget("Open folder", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Open terminal", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy path", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy Python path", "state"), "normal")
            self.assertEqual(app.context_menu.entrycget("Copy package list", "state"), "normal")
            # Python install has no activate command
            self.assertEqual(app.context_menu.entrycget("Copy activate command", "state"), "disabled")
            # Locked kinds cannot be deleted
            self.assertEqual(app.context_menu.entrycget("Delete…", "state"), "disabled")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_terminal_submenu_remembers(self) -> None:
        """Picking 'Windows PowerShell' in the submenu calls open_terminal and saves terminal='powershell'."""
        app = self._create_app(mapped=False)
        try:
            # Seed initial settings to verify they aren't lost
            init_settings = store.load_settings(self.places.settings_path)
            init_settings.drives = ["C:\\", "D:\\"]
            init_settings.terminal = "cmd"
            store.save_settings(self.places.settings_path, init_settings)

            p = os.path.join(self.tmp, "term_env")
            os.makedirs(p, exist_ok=True)
            env = Env(p, Kind.VENV, "term_env")
            app.envs[env.key] = env
            app.envs_table.upsert(env)
            app.envs_table.tree.selection_set(env.key)
            app._update_action_states()

            # Find index of "Windows PowerShell" in submenu
            end = app.terminal_submenu.index("end")
            ps_idx: int | None = None
            if end is not None:
                for idx in range(end + 1):
                    if app.terminal_submenu.entrycget(idx, "label") == "Windows PowerShell":
                        ps_idx = idx
                        break

            self.assertIsNotNone(ps_idx, "Windows PowerShell submenu entry not found")

            with patch("venvdiscover.ui.app.open_terminal") as mock_open:
                app.terminal_submenu.invoke(ps_idx)
                self.assertTrue(mock_open.called)
                called_args = mock_open.call_args[0]
                # Called with (env, terminal, places)
                self.assertEqual(called_args[0].key, env.key)
                self.assertEqual(called_args[1].id, "powershell")

            # Check that app remembers current terminal
            self.assertEqual(app.current_terminal.id, "powershell")

            # Check settings file persistence without losing drives
            saved_settings = store.load_settings(self.places.settings_path)
            self.assertEqual(saved_settings.terminal, "powershell")
            self.assertEqual(saved_settings.drives, ["C:\\", "D:\\"])
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()

    def test_copy_status_restores(self) -> None:
        """Status shows copy message, then returns to previous text after 3 s (pumped via pump_after)."""
        app = self._create_app(mapped=False)
        try:
            # Drain any initial background/scanner timers
            tk_support.pump_after(app)

            app.status_var.set("Normal Status Line")
            self.assertEqual(app.status_var.get(), "Normal Status Line")

            # Temporary status
            app._set_temporary_status("Copied path of test_env")
            self.assertEqual(app.status_var.get(), "Copied path of test_env")

            # Restore using pump_after without wall-clock sleep
            tk_support.pump_after(app)
            self.assertEqual(app.status_var.get(), "Normal Status Line")

            # Successive copies cancel previous pending restore and preserve original status
            app.status_var.set("Baseline Status")
            app._set_temporary_status("First copy")
            self.assertEqual(app.status_var.get(), "First copy")

            app._set_temporary_status("Second copy")
            self.assertEqual(app.status_var.get(), "Second copy")

            tk_support.pump_after(app)
            self.assertEqual(app.status_var.get(), "Baseline Status")

            # Copy activate command sets status "Copied activate command of <name>" and restores
            act_dir = os.path.join(self.tmp, "act_env")
            os.makedirs(os.path.join(act_dir, "Scripts"), exist_ok=True)
            with open(os.path.join(act_dir, "Scripts", "activate.bat"), "w") as f:
                f.write("")
            act_env = Env(act_dir, Kind.VENV, "act_env")
            app.envs[act_env.key] = act_env
            app.envs_table.upsert(act_env)
            app.envs_table.tree.selection_set(act_env.key)
            app._update_action_states()

            # Ensure any upsert idle tasks are clean
            tk_support.pump_after(app)

            app.status_var.set("Ready")
            act_clip: list[str] = []
            with patch.object(app, "clipboard_clear", side_effect=act_clip.clear), \
                 patch.object(app, "clipboard_append", side_effect=act_clip.append):
                app._on_copy_activate()

            self.assertEqual(app.status_var.get(), f"Copied activate command of {act_env.name}")
            tk_support.pump_after(app)
            self.assertEqual(app.status_var.get(), "Ready")
        finally:
            try:
                app.update()
            except Exception:
                pass
            app.destroy()


if __name__ == "__main__":
    unittest.main()
