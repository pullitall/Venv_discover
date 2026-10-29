"""End-to-end UI flow tests for delete, bin, purge-on-start, and clean conda cache."""

from __future__ import annotations

import gc
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
import unittest
from unittest.mock import patch

from tests import fakes
from tests.tk_support import create_test_app
from venvdiscover import store, trashbin
from venvdiscover.model import Env, Kind, longpath
from venvdiscover.scanner import EnvFound, EnvUpdated, Scanner
from venvdiscover.trashbin import BinItem, bin_root_for, list_bin
from venvdiscover.ui.app import App
from venvdiscover.ui.delete_dialog import BinChanged, Deleted
from venvdiscover.ui.format import fmt_size


class DummyScanner(threading.Thread):
    def __init__(self, **kwargs) -> None:
        super().__init__(daemon=True)

    def run(self) -> None:
        pass

    def stop(self) -> None:
        pass


def pump(root: tk.Misc, timeout: float = 4.0, condition=None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if condition and condition():
            return
        time.sleep(0.01)


class UIFlowTests(unittest.TestCase):
    def tearDown(self) -> None:
        gc.collect()

    def test_delete_restore_flow(self) -> None:
        """Real App over fake envs: delete moves to bin, disappears from table, restores back."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "play_env")
            env = Env(v_path, Kind.VENV, "play_env", size=10 * 1024)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))

                # 1. Select env and request delete
                app.envs_table.tree.selection_set(env.key)
                app.request_delete([env])

                # Wait for dialog to appear
                pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                dialog = app._active_delete_dialog
                self.assertIsNotNone(dialog)
                self.assertEqual(dialog.deletable_envs, [env])
                self.assertEqual(app.grab_current(), dialog)

                # 2. Confirm deletion by invoking Delete button on dialog
                dialog.delete_btn.invoke()

                # Wait for deletion to complete and bin tab to refresh
                pump(
                    app,
                    timeout=4.0,
                    condition=lambda: (env.key not in app.envs and len(app.bin_tab.tree.get_children()) == 1),
                )

                # Row disappears from table and dict
                self.assertNotIn(env.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env.key))

                # Original folder does not exist at original location
                self.assertFalse(os.path.exists(longpath(v_path)))

                # Bin tab shows 1 item
                children = app.bin_tab.tree.get_children()
                self.assertEqual(len(children), 1)

                # actions.log has bin entry
                with open(longpath(places.log_path), "r", encoding="utf-8") as f:
                    logs = [json.loads(line) for line in f]
                bin_logs = [entry for entry in logs if entry["action"] == "bin"]
                self.assertEqual(len(bin_logs), 1)
                self.assertEqual(bin_logs[0]["env_path"], v_path)

                # 3. Select item in bin tab and click Restore
                item_id = children[0]
                app.bin_tab.tree.selection_set(item_id)
                app.bin_tab.tree.event_generate("<<TreeviewSelect>>")
                app.update()
                app.bin_tab.restore_btn.invoke()

                # Wait for restore to complete and env to return
                pump(
                    app,
                    timeout=4.0,
                    condition=lambda: (env.key in app.envs and len(app.bin_tab.tree.get_children()) == 0),
                )

                # Row is back in table and dict
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))

                # Original folder is back at original path
                self.assertTrue(os.path.exists(longpath(v_path)))
                self.assertTrue(os.path.exists(os.path.join(v_path, "pyvenv.cfg")))

                # Bin tab is now empty
                self.assertEqual(len(app.bin_tab.tree.get_children()), 0)

                # actions.log has restore entry
                with open(longpath(places.log_path), "r", encoding="utf-8") as f:
                    logs = [json.loads(line) for line in f]
                restore_logs = [entry for entry in logs if entry["action"] == "restore"]
                self.assertEqual(len(restore_logs), 1)
                self.assertEqual(restore_logs[0]["env_path"], v_path)

            finally:
                app.destroy()

    def test_project_folder_refused(self) -> None:
        """request_delete on a project-root venv leaves folder intact, nothing in bin, and logs nothing as deleted."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # Create project-root venv: pyvenv.cfg + .git\HEAD + main.py
            proj_dir = os.path.join(tmp, "my_repo")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")

            env = Env(proj_dir, Kind.VENV, "my_repo", size=2048)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                with patch("tkinter.messagebox.showerror") as mock_err:
                    app.events.put(EnvFound(env))
                    pump(app, timeout=3.0, condition=lambda: env.key in app.envs)

                    # Request delete
                    app.request_delete([env])

                    # Wait for dialog
                    pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                    dialog = app._active_delete_dialog
                    self.assertIsNotNone(dialog)

                    # deletable_envs is empty, delete button is disabled
                    self.assertEqual(dialog.deletable_envs, [])
                    self.assertEqual(str(dialog.delete_btn.cget("state")), "disabled")

                    # Text explains why it cannot be deleted
                    self.assertIn("Can't delete:", dialog.dialog_text)
                    self.assertIn("This folder is also a project (.git). Nothing was changed.", dialog.dialog_text)

                    # Invoking disabled delete button does not confirm
                    dialog.delete_btn.invoke()
                    # Dismiss dialog with cancel button
                    dialog.cancel_btn.invoke()
                    pump(app, timeout=1.0)

                    # Worker list was empty, so worker never failed or showed error
                    mock_err.assert_not_called()

                # Project folder files are untouched
                self.assertTrue(os.path.exists(os.path.join(proj_dir, "main.py")))
                self.assertTrue(os.path.exists(os.path.join(git_dir, "HEAD")))
                self.assertTrue(os.path.exists(os.path.join(proj_dir, "pyvenv.cfg")))

                # Nothing in the bin
                b_root = bin_root_for(proj_dir, places)
                self.assertEqual(list_bin([b_root]), [])
                self.assertEqual(len(app.bin_tab.tree.get_children()), 0)

                # actions.log has no delete or move records
                if os.path.exists(longpath(places.log_path)):
                    with open(longpath(places.log_path), "r", encoding="utf-8") as f:
                        logs = [json.loads(line) for line in f]
                    mod_logs = [entry for entry in logs if entry["action"] in ("move", "delete")]
                    self.assertEqual(mod_logs, [])

            finally:
                app.destroy()

    def test_worker_does_what_confirmed_no_replan(self) -> None:
        """request_delete on 0.9 GB venv, then late size 1.2 GB arrives: env ends up in the bin, NOT permanently deleted."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "growing_venv")
            env = Env(v_path, Kind.VENV, "growing_venv", size=int(0.9 * 1024 ** 3))

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)

                # Request delete with initial 0.9 GB size
                app.request_delete([env])

                pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                dialog = app._active_delete_dialog
                self.assertIsNotNone(dialog)
                self.assertEqual(dialog.to_bin, [env])
                self.assertEqual(dialog.permanent, [])

                # Background scan updates size to 1.2 GB on the same object while dialog is open
                env.size = int(1.2 * 1024 ** 3)
                app.events.put(EnvUpdated(env))
                pump(app, timeout=2.0, condition=lambda: app.envs[env.key].size == int(1.2 * 1024 ** 3))
                self.assertEqual(app.envs[env.key].size, int(1.2 * 1024 ** 3))

                # User confirms the dialog
                dialog.delete_btn.invoke()

                # Pump until delete worker finishes and bin tab refreshes
                pump(app, timeout=4.0, condition=lambda: len(app.bin_tab.tree.get_children()) == 1)

                # Env moved to the bin (NOT permanently deleted!)
                b_root = bin_root_for(v_path, places)
                binned_items = list_bin([b_root])
                self.assertEqual(len(binned_items), 1)
                self.assertEqual(binned_items[0].original_path, v_path)
                self.assertTrue(os.path.exists(longpath(binned_items[0].env_path)))
                self.assertFalse(os.path.exists(longpath(v_path)))

            finally:
                app.destroy()

    def test_bin_tab_delete_forever_confirmation(self) -> None:
        """Delete forever asks 'Delete {name} ({size}) forever? This can't be undone.', Cancel is default; No leaves item, Yes purges."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "to_purge_env")
            env = Env(v_path, Kind.VENV, "to_purge_env", size=2048)

            # Move to bin directly to set up fixture
            log_entries: list[tuple[str, str, str]] = []
            item = trashbin.move_to_bin(
                env,
                places,
                now=time.time(),
                log=lambda a, p, d: store.append_log(places.log_path, a, p, d),
            )

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                # Wait for startup bin worker to complete
                pump(app, timeout=3.0, condition=lambda: getattr(app, "_startup_bin_done", False))
                self.assertEqual(len(app.bin_tab.tree.get_children()), 1)

                app.bin_tab.tree.selection_set(item.id)
                app.bin_tab.tree.event_generate("<<TreeviewSelect>>")
                app.update()
                self.assertEqual(str(app.bin_tab.forget_btn.cget("state")), "normal")

                expected_msg = f"Delete {item.name} ({fmt_size(item.size)}) forever? This can't be undone."

                # 1. User selects 'No' / Cancel: item remains in the bin
                with patch("venvdiscover.ui.app.messagebox.askyesno", return_value=False) as mock_ask:
                    app.bin_tab.forget_btn.invoke()
                    pump(app, timeout=1.0)
                    mock_ask.assert_called_once_with(
                        "Delete forever",
                        expected_msg,
                        default=messagebox.NO,
                        parent=app,
                    )

                # Still in bin
                b_root = bin_root_for(v_path, places)
                self.assertEqual(len(list_bin([b_root])), 1)
                self.assertEqual(len(app.bin_tab.tree.get_children()), 1)
                self.assertTrue(os.path.exists(longpath(item.env_path)))

                # 2. User selects 'Yes': item is purged permanently
                with patch("venvdiscover.ui.app.messagebox.askyesno", return_value=True) as mock_ask:
                    app.bin_tab.forget_btn.invoke()
                    pump(app, timeout=3.0, condition=lambda: len(app.bin_tab.tree.get_children()) == 0)
                    mock_ask.assert_called_once_with(
                        "Delete forever",
                        expected_msg,
                        default=messagebox.NO,
                        parent=app,
                    )

                # Gone from bin and disk
                self.assertEqual(len(list_bin([b_root])), 0)
                self.assertEqual(len(app.bin_tab.tree.get_children()), 0)
                self.assertFalse(os.path.exists(longpath(item.env_path)))

                # Purge entry in actions.log
                with open(longpath(places.log_path), "r", encoding="utf-8") as f:
                    logs = [json.loads(line) for line in f]
                purge_logs = [entry for entry in logs if entry["action"] == "purge"]
                self.assertEqual(len(purge_logs), 1)
                self.assertEqual(purge_logs[0]["env_path"], v_path)

            finally:
                app.destroy()

    def test_startup_worker_cleanup_and_purge(self) -> None:
        """On start, cleanup_leftovers and purge_expired run in a worker, then populate the Bin tab."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            now = time.time()

            # 1. Expired item (deleted 20 days ago)
            v_old = fakes.make_venv(tmp, "old_env")
            env_old = Env(v_old, Kind.VENV, "old_env", size=1024)
            trashbin.move_to_bin(
                env_old,
                places,
                now=now - (20 * 86400),
                log=lambda a, p, d: store.append_log(places.log_path, a, p, d),
            )

            # 2. Active item (deleted 2 days ago)
            v_act = fakes.make_venv(tmp, "active_env")
            env_act = Env(v_act, Kind.VENV, "active_env", size=2048)
            item_act = trashbin.move_to_bin(
                env_act,
                places,
                now=now - (2 * 86400),
                log=lambda a, p, d: store.append_log(places.log_path, a, p, d),
            )

            # 3. Orphaned .deleting-* folder
            b_root = bin_root_for(v_old, places)
            leftover_dir = os.path.join(b_root, ".deleting-123456")
            os.makedirs(longpath(leftover_dir), exist_ok=True)
            fakes.write_file(os.path.join(leftover_dir, "dummy.txt"), "leftover")

            self.assertTrue(os.path.exists(longpath(leftover_dir)))

            # Start App
            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                # Wait for startup bin worker to complete
                pump(app, timeout=4.0, condition=lambda: len(app.bin_tab.tree.get_children()) == 1)

                # Orphaned folder cleaned up
                self.assertFalse(os.path.exists(longpath(leftover_dir)))

                # Expired item purged from bin
                binned = list_bin([b_root])
                self.assertEqual(len(binned), 1)
                self.assertEqual(binned[0].id, item_act.id)

                # Active item displayed in Bin tab
                children = app.bin_tab.tree.get_children()
                self.assertEqual(len(children), 1)
                self.assertEqual(children[0], item_act.id)

            finally:
                app.destroy()

    def test_clean_conda_cache_flow(self) -> None:
        """Clean conda cache button asks confirmation first, runs run_conda_clean, and streams output."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            conda_root = fakes.make_conda_root(tmp, "miniconda_test")
            fakes.write_file(os.path.join(conda_root, "condabin", "conda.bat"), "@echo off\n")
            base_env = Env(conda_root, Kind.CONDA_BASE, "base", size=100 * 1024 * 1024)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                # Initially disabled without conda base
                self.assertEqual(str(app.btn_clean.cget("state")), "disabled")

                # Feed conda base
                app.events.put(EnvFound(base_env))
                pump(app, timeout=3.0, condition=lambda: str(app.btn_clean.cget("state")) == "normal")
                self.assertEqual(str(app.btn_clean.cget("state")), "normal")

                # 1. User cancels confirmation -> nothing runs
                with patch("venvdiscover.ui.app.messagebox.askyesno", return_value=False) as mock_ask, \
                     patch("venvdiscover.ui.app.run_conda_clean") as mock_clean:
                    app.btn_clean.invoke()
                    pump(app, timeout=0.5)
                    mock_ask.assert_called_once()
                    mock_clean.assert_not_called()

                # 2. User confirms -> streams output into dialog
                output_lines = ["Removing cached packages…", "12 packages removed.", "Done."]

                def fake_clean(exe, on_line):
                    for line in output_lines:
                        on_line(line)
                    return 0

                with patch("venvdiscover.ui.app.messagebox.askyesno", return_value=True) as mock_ask, \
                     patch("venvdiscover.ui.app.run_conda_clean", side_effect=fake_clean) as mock_clean:
                    app.btn_clean.invoke()
                    pump(app, timeout=3.0, condition=lambda: os.path.exists(longpath(places.log_path)))
                    mock_ask.assert_called_once()
                    mock_clean.assert_called_once()

                # Check actions.log has clean_conda entry
                with open(longpath(places.log_path), "r", encoding="utf-8") as f:
                    logs = [json.loads(line) for line in f]
                clean_logs = [entry for entry in logs if entry["action"] == "clean_conda"]
                self.assertGreaterEqual(len(clean_logs), 1)
                self.assertEqual(clean_logs[0]["env_path"], conda_root)

            finally:
                app.destroy()

    def test_request_delete_measures_when_size_none(self) -> None:
        """request_delete measures any env with size None and displays formatted size in confirm dialog."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "unmeasured_venv")
            env = Env(v_path, Kind.VENV, "unmeasured_venv", size=None)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)
                self.assertIsNone(app.envs[env.key].size)

                # Request delete
                app.request_delete([env])

                # Wait for dialog
                pump(app, timeout=4.0, condition=lambda: app._active_delete_dialog is not None)
                dialog = app._active_delete_dialog
                self.assertIsNotNone(dialog)

                # Dialog contains measured size
                self.assertIn("Goes to the bin (14 days):", dialog.dialog_text)
                self.assertIn("unmeasured_venv", dialog.dialog_text)
                self.assertNotIn("None", dialog.dialog_text)

                dialog.cancel_btn.invoke()
                pump(app, timeout=1.0)

            finally:
                app.destroy()

    def test_one_delete_at_a_time(self) -> None:
        """With move_to_bin slowed, confirm a delete, then post an unrelated BinChanged:
        Delete stays disabled, request_delete and _on_delete open no second dialog;
        after the worker finishes, Delete is enabled again."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            p1 = fakes.make_venv(tmp, "env1")
            p2 = fakes.make_venv(tmp, "env2")
            env1 = Env(p1, Kind.VENV, "env1", size=1024)
            env2 = Env(p2, Kind.VENV, "env2", size=2048)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env1))
                app.events.put(EnvFound(env2))
                pump(app, timeout=3.0, condition=lambda: env1.key in app.envs and env2.key in app.envs)

                # Select env1
                app.envs_table.tree.selection_set(env1.key)
                app.envs_table.tree.event_generate("<<TreeviewSelect>>")
                pump(app, timeout=1.0)
                self.assertEqual(str(app.btn_delete.cget("state")), "normal")

                # Slow down move_to_bin using a synchronization barrier/event
                proceed_event = threading.Event()
                started_event = threading.Event()
                real_move = trashbin.move_to_bin

                def slow_move(*args, **kw):
                    started_event.set()
                    proceed_event.wait(timeout=5.0)
                    return real_move(*args, **kw)

                with patch("venvdiscover.ui.delete_dialog.move_to_bin", side_effect=slow_move):
                    app.request_delete([env1])
                    pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                    dialog = app._active_delete_dialog
                    self.assertIsNotNone(dialog)

                    # Confirm delete
                    dialog.delete_btn.invoke()

                    # Wait until slow_move has started inside worker thread
                    self.assertTrue(started_event.wait(timeout=3.0))

                    # Post an unrelated BinChanged event while worker is actively running
                    app.events.put(BinChanged())
                    pump(app, timeout=0.2)

                    # Delete must remain disabled
                    self.assertEqual(str(app.btn_delete.cget("state")), "disabled")

                    # request_delete and _on_delete open no second dialog
                    app.request_delete([env2])
                    pump(app, timeout=0.1)
                    self.assertIsNone(app._active_delete_dialog)

                    app._on_delete()
                    pump(app, timeout=0.1)
                    self.assertIsNone(app._active_delete_dialog)

                    # Now let the slow worker complete
                    proceed_event.set()

                    # Pump until DeleteFinished is handled and env1 is deleted
                    pump(
                        app,
                        timeout=4.0,
                        condition=lambda: (
                            env1.key not in app.envs
                            and not app._delete_worker_running
                            and not app._delete_in_progress
                        ),
                    )

                # Select env2
                app.envs_table.tree.selection_set(env2.key)
                app.envs_table.tree.event_generate("<<TreeviewSelect>>")
                pump(app, timeout=1.0)

                # Delete button is enabled again
                self.assertEqual(str(app.btn_delete.cget("state")), "normal")
            finally:
                app.destroy()

    def test_deleted_env_ghost_row_ignored(self) -> None:
        """Deleted(A), then EnvUpdated(A) with the folder gone, gives no row. After a restore, the row is back."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "ghost_env")
            env = Env(v_path, Kind.VENV, "ghost_env", size=4096)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)
                self.assertIn(env.key, app.envs)

                # Delete env
                app.request_delete([env])
                pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                app._active_delete_dialog.delete_btn.invoke()
                pump(app, timeout=4.0, condition=lambda: env.key not in app.envs)

                self.assertNotIn(env.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env.key))
                self.assertFalse(os.path.exists(longpath(v_path)))

                # Late EnvUpdated arrives for the deleted env whose folder is gone
                app.events.put(EnvUpdated(env))
                pump(app, timeout=0.5)

                # No ghost row appears!
                self.assertNotIn(env.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env.key))

                # Now restore the item from bin
                b_root = bin_root_for(v_path, places)
                items = list_bin([b_root])
                self.assertEqual(len(items), 1)

                app._on_bin_restore(items[0])
                pump(app, timeout=4.0, condition=lambda: env.key in app.envs)

                # Row is back in table!
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))
                self.assertTrue(os.path.exists(longpath(v_path)))
            finally:
                app.destroy()

    def test_delete_key_triggers_request_delete(self) -> None:
        """tree.event_generate('<Delete>') invokes request_delete with selected env."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_path = fakes.make_venv(tmp, "del_key_env")
            env = Env(v_path, Kind.VENV, "del_key_env", size=1024)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)

                app.envs_table.tree.selection_set(env.key)
                app.envs_table.tree.event_generate("<<TreeviewSelect>>")
                pump(app, timeout=0.5)

                with patch.object(app, "request_delete") as mock_req:
                    app.focus_force()
                    app.envs_table.tree.focus_force()
                    app.update()
                    app.envs_table.tree.event_generate("<Delete>")
                    pump(app, timeout=0.5)
                    mock_req.assert_called_once_with([env])
            finally:
                app.destroy()

    def test_ghost_check_long_path(self) -> None:
        """Ghost check handles long paths over 260 characters when env is recreated."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            deep_parent = os.path.join(tmp, "Kosztorysowy Pomocnik Majstra", "żabka", *(["sub" * 10] * 6))
            v_path = fakes.make_venv(deep_parent, ".venv")
            self.assertGreater(len(v_path), 260)
            env = Env(v_path, Kind.VENV, ".venv", size=4096)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                # Initially present
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))

                # Deleted event posts
                app.events.put(Deleted(env.key, None))
                pump(app, timeout=3.0, condition=lambda: env.key not in app.envs)
                self.assertNotIn(env.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env.key))
                self.assertIn(env.key, app._deleted_keys)

                # Recreate the folder at the long path
                fakes.make_venv(deep_parent, ".venv")
                self.assertTrue(os.path.isdir(longpath(v_path)))

                # Post EnvFound(env)
                app.events.put(EnvFound(env))
                pump(app, timeout=3.0, condition=lambda: env.key in app.envs)

                # Row is back!
                self.assertIn(env.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env.key))
                self.assertNotIn(env.key, app._deleted_keys)
            finally:
                app.destroy()

    def test_worker_gets_only_deletable_envs(self) -> None:
        """Worker receives only dialog.deletable_envs; project folders are excluded and showerror is never called."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. Project-root venv: pyvenv.cfg + .git\\HEAD + main.py
            proj_dir = os.path.join(tmp, "proj_env")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")
            env_proj = Env(proj_dir, Kind.VENV, "proj_env", size=5000)

            # 2. Normal deletable venv
            norm_dir = fakes.make_venv(tmp, "normal_env")
            env_normal = Env(norm_dir, Kind.VENV, "normal_env", size=5000)

            app = create_test_app(
                places,
                drive_roots=[tmp],
                scanner_factory=lambda **kw: DummyScanner(),
                mapped=True,
            )
            try:
                app.events.put(EnvFound(env_proj))
                app.events.put(EnvFound(env_normal))
                pump(app, timeout=3.0, condition=lambda: env_proj.key in app.envs and env_normal.key in app.envs)

                # Request delete with BOTH envs
                app.request_delete([env_proj, env_normal])
                pump(app, timeout=3.0, condition=lambda: app._active_delete_dialog is not None)
                dialog = app._active_delete_dialog
                self.assertIsNotNone(dialog)

                # The dialog filters out project env into cant_delete and keeps normal in deletable_envs
                self.assertEqual(dialog.deletable_envs, [env_normal])
                self.assertEqual([e for e, _ in dialog.cant_delete], [env_proj])
                self.assertEqual(str(dialog.delete_btn.cget("state")), "normal")

                with patch("tkinter.messagebox.showerror") as mock_err:
                    dialog.delete_btn.invoke()
                    pump(app, timeout=4.0, condition=lambda: env_normal.key not in app.envs)

                    # showerror is never called!
                    mock_err.assert_not_called()

                # Normal env is in bin
                b_root = bin_root_for(norm_dir, places)
                items = list_bin([b_root])
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].name, "normal_env")
                self.assertNotIn(env_normal.key, app.envs)
                self.assertFalse(app.envs_table.tree.exists(env_normal.key))

                # Project folder is completely unchanged
                self.assertTrue(os.path.isfile(os.path.join(proj_dir, "pyvenv.cfg")))
                self.assertTrue(os.path.isfile(os.path.join(proj_dir, "main.py")))
                self.assertTrue(os.path.isfile(os.path.join(git_dir, "HEAD")))
                self.assertIn(env_proj.key, app.envs)
                self.assertTrue(app.envs_table.tree.exists(env_proj.key))
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
