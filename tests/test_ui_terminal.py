"""Unit tests for the terminal button, menu, settings persistence, and error handling in UI."""

from __future__ import annotations

import gc
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from tests import fakes
from venvdiscover import store, terminal
from venvdiscover.model import Env, Kind, norm_key
from venvdiscover.ui.app import App, DrivesDialog
from venvdiscover.walker import Drive


class DummyScanner:
    def __init__(self, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass


class UITerminalTests(unittest.TestCase):
    def tearDown(self) -> None:
        gc.collect()

    def test_open_terminal_calls_core(self) -> None:
        """With terminal.open_terminal patched, selecting one fake venv and pressing button calls it.
        With 0 or 2 envs selected, button is disabled.
        """
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v1_path = fakes.make_venv(tmp, "env1")
            v2_path = fakes.make_venv(tmp, "env2")
            env1 = Env(v1_path, Kind.VENV, "env1")
            env2 = Env(v2_path, Kind.VENV, "env2")

            app = App(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.envs[env1.key] = env1
                app.envs_table.upsert(env1)
                app.envs[env2.key] = env2
                app.envs_table.upsert(env2)

                # 0 selected: disabled
                app.envs_table.tree.selection_set([])
                app._update_action_states()
                self.assertEqual(str(app.btn_terminal["state"]), "disabled")

                # 1 selected: enabled
                app.envs_table.tree.selection_set([env1.key])
                app._update_action_states()
                self.assertEqual(str(app.btn_terminal["state"]), "normal")

                with patch("venvdiscover.ui.app.open_terminal") as mock_open:
                    app.btn_terminal.invoke()
                    mock_open.assert_called_once_with(env1, app.current_terminal, places)

                # 2 selected: disabled
                app.envs_table.tree.selection_set([env1.key, env2.key])
                app._update_action_states()
                self.assertEqual(str(app.btn_terminal["state"]), "disabled")

                # Locked env (e.g. Python install): still enabled because it's read-only
                py_path = os.path.join(tmp, "Python313")
                os.makedirs(py_path, exist_ok=True)
                env_py = Env(py_path, Kind.PYTHON, "Python313")
                app.envs[env_py.key] = env_py
                app.envs_table.upsert(env_py)
                app.envs_table.tree.selection_set([env_py.key])
                app._update_action_states()
                self.assertEqual(str(app.btn_terminal["state"]), "normal")
            finally:
                app.destroy()
                del app
                gc.collect()

    def test_terminal_choice_saved(self) -> None:
        """Pick powershell: settings has terminal == 'powershell' and drives unchanged.
        Save drives: terminal is still 'powershell'.
        Old settings file without terminal gives 'cmd'.
        """
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            # Initial settings with specific drives
            initial_settings = store.Settings(drives=["C:\\", "D:\\"], terminal="cmd")
            store.save_settings(places.settings_path, initial_settings)

            app = App(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                ps_term = next((t for t in app.terminals if t.id == "powershell"), None)
                if ps_term is None:
                    ps_term = terminal.Terminal("powershell", "Windows PowerShell", "powershell.exe")
                    app.terminals.append(ps_term)

                # 1. Pick powershell
                app._on_select_terminal(ps_term)
                self.assertEqual(app.current_terminal.id, "powershell")
                self.assertEqual(app.mb_terminal.cget("text"), f"{ps_term.label} \u25be")

                s1 = store.load_settings(places.settings_path)
                self.assertEqual(s1.terminal, "powershell")
                self.assertEqual(s1.drives, ["C:\\", "D:\\"])

                # 2. Save drives via DrivesDialog
                fake_drives = [
                    Drive("C:\\", "NTFS", "OS"),
                    Drive("E:\\", "NTFS", "Data"),
                ]
                with patch("venvdiscover.ui.app.local_drives", return_value=fake_drives):
                    dlg = DrivesDialog(app)
                    dlg.withdraw()
                    # Tick only E:\
                    dlg.check_vars[0].set(False)
                    dlg.check_vars[1].set(True)
                    dlg.on_save()

                s2 = store.load_settings(places.settings_path)
                self.assertEqual(s2.drives, ["E:\\"])
                self.assertEqual(s2.terminal, "powershell")

                # 3. Old settings without terminal field gives 'cmd'
                old_file = os.path.join(tmp, "old_settings.json")
                fakes.write_file(old_file, json.dumps({"drives": ["Z:\\"]}))
                s_old = store.load_settings(old_file)
                self.assertEqual(s_old.drives, ["Z:\\"])
                self.assertEqual(s_old.terminal, "cmd")
            finally:
                app.destroy()
                del app
                gc.collect()

    def test_saved_terminal_missing_falls_back_to_cmd(self) -> None:
        """If the saved terminal is no longer available, fall back to Command Prompt."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            store.save_settings(places.settings_path, store.Settings(terminal="pwsh"))

            # Fake environ with no pwsh
            fake_env = {"SYSTEMROOT": "C:\\Windows"}
            app = App(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
                environ=fake_env,
            )
            app.withdraw()
            try:
                self.assertEqual(app.current_terminal.id, "cmd")
                self.assertEqual(app.mb_terminal.cget("text"), "Command Prompt \u25be")
            finally:
                app.destroy()
                del app
                gc.collect()

    def test_missing_terminal_message(self) -> None:
        """open_terminal raising FileNotFoundError shows '<label> wasn't found. Pick another terminal.'.
        Any other OSError shows '<label> couldn't be started: <error>'.
        """
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "env1")
            env1 = Env(v_path, Kind.VENV, "env1")

            app = App(
                places,
                drive_roots=[],
                scanner_factory=lambda **kw: DummyScanner(),
            )
            app.withdraw()
            try:
                app.envs[env1.key] = env1
                app.envs_table.upsert(env1)
                app.envs_table.tree.selection_set([env1.key])
                app._update_action_states()

                # 1. FileNotFoundError
                with patch("venvdiscover.ui.app.open_terminal", side_effect=FileNotFoundError("missing exe")), \
                     patch("venvdiscover.ui.app.messagebox.showerror") as mock_box:
                    app.btn_terminal.invoke()
                    mock_box.assert_called_once_with(
                        "Error",
                        f"{app.current_terminal.label} wasn't found. Pick another terminal.",
                        parent=app,
                    )

                # 2. PermissionError / other OSError
                with patch("venvdiscover.ui.app.open_terminal", side_effect=PermissionError("Access is denied")), \
                     patch("venvdiscover.ui.app.messagebox.showerror") as mock_box:
                    app.btn_terminal.invoke()
                    mock_box.assert_called_once_with(
                        "Error",
                        f"{app.current_terminal.label} couldn't be started: Access is denied",
                        parent=app,
                    )
            finally:
                app.destroy()
                del app
                gc.collect()


if __name__ == "__main__":
    unittest.main()
