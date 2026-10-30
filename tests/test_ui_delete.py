"""Tests for delete confirmation dialog, delete worker, and Bin tab."""

from __future__ import annotations

import gc
import os
import queue
import tkinter as tk
import unittest

from tests import fakes, tk_support
from venvdiscover.model import Env, Kind, longpath
from venvdiscover.trashbin import BinItem, bin_root_for
from venvdiscover.ui.bin_tab import BinTab
from venvdiscover.ui.delete_dialog import (
    BinChanged,
    ConfirmDeleteDialog,
    DeleteFailed,
    DeleteWorker,
    Deleted,
    Measured,
)


class DeleteUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk_support.create_test_root()
        self.logs: list[tuple[str, str, str]] = []

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass
        del self.root
        gc.collect()

    def log(self, action: str, env_path: str, detail: str) -> None:
        self.logs.append((action, env_path, detail))

    def test_dialog_text(self) -> None:
        """With one 2 GB env, dialog shows exact over-limit text, and Cancel doesn't confirm."""
        with fakes.tmpdir() as tmp:
            p = os.path.join(tmp, "large_env")
            os.makedirs(longpath(p), exist_ok=True)
            env_large = Env(p, Kind.VENV, "large_env", size=2 * 1024 ** 3)

            confirmed = False

            def on_confirm() -> None:
                nonlocal confirmed
                confirmed = True

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=[],
                permanent=[env_large],
                locked=[],
                extras={},
                on_confirm=on_confirm,
            )
            dialog.update()

            # Check exact over-limit text
            text = dialog.dialog_text
            self.assertIn("Deleted permanently:", text)
            self.assertIn("This is 2.0 GB and can only be deleted permanently.", text)

            # Cancel button has focus by default and does not call on_confirm
            self.assertEqual(dialog.focus_lastfor(), dialog.cancel_btn)
            dialog.cancel_btn.invoke()
            dialog.update()
            self.assertFalse(confirmed)

            # Check conda and app-owned notes in dialog
            p_conda = os.path.join(tmp, "conda_env")
            p_app = os.path.join(tmp, "app_env")
            os.makedirs(longpath(p_conda), exist_ok=True)
            os.makedirs(longpath(p_app), exist_ok=True)

            env_conda = Env(p_conda, Kind.CONDA, "conda_env", size=100 * 1024 * 1024)
            env_app = Env(p_app, Kind.APP_VENV, "app_env", size=50 * 1024 * 1024)

            dialog2 = ConfirmDeleteDialog(
                self.root,
                to_bin=[env_conda, env_app],
                permanent=[],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog2.update()
            text2 = dialog2.dialog_text
            self.assertIn(
                "Conda shares files with its package cache, so deleting may free less space than shown.",
                text2,
            )
            self.assertIn(
                "This env belongs to an app. Deleting it may break that app until it rebuilds the env.",
                text2,
            )
            dialog2.destroy()

    def test_dialog_text_shows_what_deleting_frees(self) -> None:
        """A measured env that shares files says exactly what deleting frees, instead of the generic conda note."""
        with fakes.tmpdir() as tmp:
            p = os.path.join(tmp, "conda_linked")
            os.makedirs(longpath(p), exist_ok=True)
            gb = 1024 ** 3
            env = Env(p, Kind.CONDA, "conda_linked", size=3 * gb, size_unique=gb)

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=[],
                permanent=[env],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog.update()
            text = dialog.dialog_text
            self.assertIn(f"conda_linked — {p} — frees 1.0 GB (3.0 GB on disk, 2.0 GB shared with other folders)", text)
            # The bin-or-permanent rule still uses the size on disk
            self.assertIn("This is 3.0 GB and can only be deleted permanently.", text)
            self.assertNotIn("may free less space than shown", text)
            dialog.destroy()

    def test_dialog_extras_and_locked(self) -> None:
        """Extras (kernels, shortcuts) and locked envs (e.g. conda base) appear in dialog text."""
        with fakes.tmpdir() as tmp:
            p = os.path.join(tmp, "test_env")
            os.makedirs(longpath(p), exist_ok=True)
            env = Env(p, Kind.VENV, "test_env", size=1024)

            kernel_dir = os.path.join(tmp, "share", "jupyter", "kernels", "myenv")
            lnk_path = os.path.join(tmp, "Start Menu", "Programs", "myenv.lnk")

            base_root = fakes.make_conda_root(tmp, "miniconda_base")
            locked_base = Env(base_root, Kind.CONDA_BASE, "base", size=5000)

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=[env],
                permanent=[],
                locked=[locked_base],
                extras={env.key: [kernel_dir, lnk_path]},
                on_confirm=lambda: None,
            )
            dialog.update()
            text = dialog.dialog_text

            # Check that both extras paths appear
            self.assertIn(kernel_dir, text)
            self.assertIn(lnk_path, text)

            # Check that locked conda base appears under "Can't delete" with its path
            self.assertIn("Can't delete:", text)
            self.assertIn(locked_base.path, text)
            self.assertIn("Locked environment (conda base). Cannot be deleted.", text)
            dialog.destroy()

    def test_worker_measure_then_delete(self) -> None:
        """On a fake small venv, measure posts Measured, delete posts Deleted with item and BinChanged."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "small_venv")
            env = Env(v, Kind.VENV, "small_venv", size=None)

            q: queue.Queue = queue.Queue()

            # 1. Measure mode
            worker_m = DeleteWorker("measure", [env], places, q, self.log)
            worker_m.start()
            worker_m.join(timeout=5.0)
            self.assertFalse(worker_m.is_alive())

            ev1 = q.get_nowait()
            self.assertIsInstance(ev1, Measured)
            self.assertEqual(ev1.key, env.key)
            self.assertGreater(ev1.size, 0)
            self.assertIsNone(ev1.project_marker)

            # Update size on env
            env.size = ev1.size

            # 2. Delete mode
            worker_d = DeleteWorker("delete", [env], places, q, self.log)
            worker_d.start()
            worker_d.join(timeout=5.0)
            self.assertFalse(worker_d.is_alive())

            ev2 = q.get_nowait()
            self.assertIsInstance(ev2, Deleted)
            self.assertEqual(ev2.key, env.key)
            self.assertIsNotNone(ev2.item)
            self.assertIsInstance(ev2.item, BinItem)

            ev3 = q.get_nowait()
            self.assertIsInstance(ev3, BinChanged)

            # Folder has moved into the bin
            self.assertFalse(os.path.exists(longpath(env.path)))
            self.assertTrue(os.path.exists(longpath(ev2.item.env_path)))

    def test_worker_measure_project_marker(self) -> None:
        """On a fake project-root venv, DeleteWorker measure step reports project_marker."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            proj_dir = os.path.join(tmp, "project_measure")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")

            env = Env(proj_dir, Kind.VENV, "project_measure", size=None)

            q: queue.Queue = queue.Queue()
            worker = DeleteWorker("measure", [env], places, q, self.log)
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive())

            ev = q.get_nowait()
            self.assertIsInstance(ev, Measured)
            self.assertEqual(ev.key, env.key)
            self.assertIn(ev.project_marker, {".git", "main.py"})

    def test_worker_follows_confirmation(self) -> None:
        """Worker moves to bin when not in permanent_keys even if env.size changed to > 1 GB after confirmation."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "late_size_venv")
            # Initially 0.9 GB (under 1 GB limit, confirmed for bin)
            env = Env(v, Kind.VENV, "late_size_venv", size=int(0.9 * 1024 ** 3))

            # Simulate background scan updating size to 1.2 GB while dialog was open
            env.size = int(1.2 * 1024 ** 3)

            q: queue.Queue = queue.Queue()
            # Caller passes permanent_keys as empty set because user confirmed it for the bin
            worker = DeleteWorker("delete", [env], places, q, self.log, permanent_keys=set())
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive())

            ev1 = q.get_nowait()
            self.assertIsInstance(ev1, Deleted)
            self.assertEqual(ev1.key, env.key)
            self.assertIsNotNone(ev1.item)  # Moved to bin, NOT permanently deleted!

            ev2 = q.get_nowait()
            self.assertIsInstance(ev2, BinChanged)

            # Folder is in the bin
            self.assertFalse(os.path.exists(longpath(env.path)))
            self.assertTrue(os.path.exists(longpath(ev1.item.env_path)))

    def test_worker_permanent_keys(self) -> None:
        """Worker permanently deletes envs whose key is in permanent_keys."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "perm_venv")
            env = Env(v, Kind.VENV, "perm_venv", size=2 * 1024 ** 3)

            q: queue.Queue = queue.Queue()
            worker = DeleteWorker("delete", [env], places, q, self.log, permanent_keys={env.key})
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive())

            ev1 = q.get_nowait()
            self.assertIsInstance(ev1, Deleted)
            self.assertEqual(ev1.key, env.key)
            self.assertIsNone(ev1.item)  # item=None indicates permanent delete

            ev2 = q.get_nowait()
            self.assertIsInstance(ev2, BinChanged)

            # Folder is completely gone and not in any bin
            self.assertFalse(os.path.exists(longpath(env.path)))

    def test_worker_in_use(self) -> None:
        """With a file held open, worker posts DeleteFailed with exact rename-refused text."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "locked_venv")
            env = Env(v, Kind.VENV, "locked_venv", size=1024)

            q: queue.Queue = queue.Queue()

            in_use_file = os.path.join(v, "Scripts", "python.exe")
            with open(longpath(in_use_file), "r+b"):
                worker = DeleteWorker("delete", [env], places, q, self.log)
                worker.start()
                worker.join(timeout=5.0)
                self.assertFalse(worker.is_alive())

            ev1 = q.get_nowait()
            self.assertIsInstance(ev1, DeleteFailed)
            self.assertEqual(ev1.key, env.key)
            expected_msg = (
                "This env is in use, or Windows needs admin rights to move it. "
                "Close programs using it and try again. Nothing was changed."
            )
            self.assertEqual(ev1.message, expected_msg)

            ev2 = q.get_nowait()
            self.assertIsInstance(ev2, BinChanged)

            # Folder is still complete
            self.assertTrue(os.path.exists(longpath(in_use_file)))

    def test_bin_tab_rows(self) -> None:
        """Two items give two rows, days left shows 14 and 3, and action buttons work."""
        tab = BinTab(self.root, on_restore=lambda item: restored.append(item), on_forget=lambda item: forgotten.append(item))
        tab.pack(fill="both", expand=True)
        self.root.update()

        restored: list[BinItem] = []
        forgotten: list[BinItem] = []

        now = 1793000000.0
        item1 = BinItem(
            id="item-1",
            bin_root="C:\\test\\bin",
            original_path="C:\\test\\env1",
            kind=Kind.VENV,
            name="env1",
            size=10 * 1024 * 1024,
            deleted_at=now - 100,  # Just deleted -> 14 days left
            conda_root=None,
            had_env_line=False,
            moved=[],
        )
        item2 = BinItem(
            id="item-2",
            bin_root="C:\\test\\bin",
            original_path="C:\\test\\env2",
            kind=Kind.VENV,
            name="env2",
            size=50 * 1024 * 1024,
            deleted_at=now - (11 * 86400),  # Deleted 11 days ago -> 3 days left
            conda_root=None,
            had_env_line=False,
            moved=[],
        )

        tab.set_items([item1, item2], now)
        self.root.update()

        children = tab.tree.get_children()
        self.assertEqual(len(children), 2)

        # Check days left column values
        val1 = tab.tree.set(item1.id, "days left")
        val2 = tab.tree.set(item2.id, "days left")
        self.assertEqual(val1, "14")
        self.assertEqual(val2, "3")

        # Test restore button
        tab.tree.selection_set(item1.id)
        self.root.update()
        tab.restore_btn.invoke()
        self.assertEqual(restored, [item1])

        # Test forget button
        tab.tree.selection_set(item2.id)
        self.root.update()
        tab.forget_btn.invoke()
        self.assertEqual(forgotten, [item2])

    def test_project_folder_refused(self) -> None:
        """Select a project-root venv together with a normal venv. Dialog shows 1 to bin and 1 can't delete.

        After confirming, the project folder still has main.py and .git, and only the normal venv is in the bin.
        If only project folder is selected, Delete button is disabled.
        """
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. Project root venv: pyvenv.cfg + .git\HEAD + main.py
            proj_dir = os.path.join(tmp, "my_project")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")

            proj_env = Env(proj_dir, Kind.VENV, "my_project", size=2048)

            # 2. Normal venv
            normal_venv = fakes.make_venv(tmp, "normal_venv")
            normal_env = Env(normal_venv, Kind.VENV, "normal_venv", size=4096)

            to_bin = [proj_env, normal_env]
            permanent: list[Env] = []
            locked: list[Env] = []

            confirmed = False

            def on_confirm() -> None:
                nonlocal confirmed
                confirmed = True
                worker = DeleteWorker(
                    "delete",
                    dialog.deletable_envs,
                    places,
                    queue.Queue(),
                    self.log,
                    permanent_keys={e.key for e in dialog.permanent},
                )
                worker.start()
                worker.join(5.0)

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=to_bin,
                permanent=permanent,
                locked=locked,
                extras={},
                on_confirm=on_confirm,
            )
            dialog.update()

            # Dialog text shows 1 to bin and 1 can't delete
            text = dialog.dialog_text
            self.assertIn("Goes to the bin (14 days):", text)
            self.assertIn("normal_venv", text)
            self.assertIn("Can't delete:", text)
            self.assertIn(proj_env.path, text)
            self.assertIn("This folder is also a project (.git). Nothing was changed.", text)

            # Confirm and execute
            self.assertEqual(str(dialog.delete_btn.cget("state")), "normal")
            dialog.delete_btn.invoke()
            dialog.update()
            self.assertTrue(confirmed)

            # Project folder still has main.py and .git intact
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "main.py")))
            self.assertTrue(os.path.exists(os.path.join(git_dir, "HEAD")))
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "pyvenv.cfg")))

            # Only normal venv is in the bin
            self.assertFalse(os.path.exists(longpath(normal_venv)))
            b_root = bin_root_for(normal_venv, places)
            self.assertTrue(os.path.exists(longpath(b_root)))

            # If nothing deletable is left, Delete button is disabled
            dialog_only_proj = ConfirmDeleteDialog(
                self.root,
                to_bin=[proj_env],
                permanent=[],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog_only_proj.update()
            self.assertEqual(str(dialog_only_proj.delete_btn.cget("state")), "disabled")
            dialog_only_proj.destroy()

    def test_project_folder_in_permanent_refused(self) -> None:
        """A 5 GB project-root venv passed in permanent must land in 'Can't delete', not permanent."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 5 GB project-root venv
            proj_dir = os.path.join(tmp, "large_project")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")

            proj_env = Env(proj_dir, Kind.VENV, "large_project", size=5 * 1024 ** 3)

            confirmed = False

            def on_confirm() -> None:
                nonlocal confirmed
                confirmed = True

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=[],
                permanent=[proj_env],
                locked=[],
                extras={},
                on_confirm=on_confirm,
            )
            dialog.update()

            text = dialog.dialog_text
            # Must NOT appear under Deleted permanently
            self.assertNotIn("Deleted permanently:", text)
            # Must appear under Can't delete
            self.assertIn("Can't delete:", text)
            self.assertIn(proj_env.path, text)
            self.assertIn("This folder is also a project (.git). Nothing was changed.", text)

            # Delete button is disabled because no deletable envs remain
            self.assertEqual(str(dialog.delete_btn.cget("state")), "disabled")
            dialog.delete_btn.invoke()
            dialog.update()
            self.assertFalse(confirmed)

            # Project folder is still complete and intact
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "main.py")))
            self.assertTrue(os.path.exists(os.path.join(git_dir, "HEAD")))
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "pyvenv.cfg")))
            dialog.destroy()

    def test_deletable_envs(self) -> None:
        """deletable_envs contains both to_bin and permanent, and excludes project folders from either."""
        with fakes.tmpdir() as tmp:
            # 1. small env (to_bin)
            p_small = fakes.make_venv(tmp, "small_env")
            env_small = Env(p_small, Kind.VENV, "small_env", size=1024 * 1024)

            # 2. 2 GB env (permanent)
            p_big = fakes.make_venv(tmp, "big_env")
            env_big = Env(p_big, Kind.VENV, "big_env", size=2 * 1024 ** 3)

            dialog = ConfirmDeleteDialog(
                self.root,
                to_bin=[env_small],
                permanent=[env_big],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog.update()
            self.assertEqual(dialog.deletable_envs, [env_small, env_big])
            dialog.destroy()

            # 3. Project-root venv: pyvenv.cfg + .git\\HEAD + main.py
            proj_dir = os.path.join(tmp, "proj_env")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")
            env_proj = Env(proj_dir, Kind.VENV, "proj_env", size=5000)

            # Adding project-root venv to to_bin leaves it out of deletable_envs
            dialog_with_proj_to_bin = ConfirmDeleteDialog(
                self.root,
                to_bin=[env_small, env_proj],
                permanent=[env_big],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog_with_proj_to_bin.update()
            self.assertEqual(dialog_with_proj_to_bin.deletable_envs, [env_small, env_big])
            dialog_with_proj_to_bin.destroy()

            # Adding project-root venv to permanent leaves it out of deletable_envs
            env_proj_big = Env(proj_dir, Kind.VENV, "proj_env", size=2 * 1024 ** 3)
            dialog_with_proj_perm = ConfirmDeleteDialog(
                self.root,
                to_bin=[env_small],
                permanent=[env_big, env_proj_big],
                locked=[],
                extras={},
                on_confirm=lambda: None,
            )
            dialog_with_proj_perm.update()
            self.assertEqual(dialog_with_proj_perm.deletable_envs, [env_small, env_big])
            dialog_with_proj_perm.destroy()


if __name__ == "__main__":
    unittest.main()
