import json
import math
import os
import shutil
import stat
import time
import unittest
from unittest.mock import patch

from tests import fakes
from venvdiscover.connections import add_env_line, kernel_dirs
from venvdiscover.model import Env, Kind, longpath, norm_key
from venvdiscover.trashbin import (
    BIN_LIMIT,
    KEEP_DAYS,
    BinItem,
    DeleteError,
    LockedError,
    bin_root_for,
    bin_roots,
    cleanup_leftovers,
    delete_permanently,
    list_bin,
    move_to_bin,
    plan_delete,
    purge,
    purge_expired,
    restore,
)


class TrashbinTests(unittest.TestCase):
    def test_plan_delete(self):
        env_10m = Env(r"C:\e1", Kind.VENV, "e1", size=10 * 1024 * 1024)
        env_1g = Env(r"C:\e2", Kind.VENV, "e2", size=BIN_LIMIT)
        env_1g_plus = Env(r"C:\e3", Kind.VENV, "e3", size=BIN_LIMIT + 1)
        base = Env(r"C:\base", Kind.CONDA_BASE, "base", size=100)
        unmeasured = Env(r"C:\e4", Kind.VENV, "e4", size=None)

        to_bin, permanent, locked = plan_delete([env_10m, env_1g, env_1g_plus, base])
        self.assertEqual(to_bin, [env_10m, env_1g])
        self.assertEqual(permanent, [env_1g_plus])
        self.assertEqual(locked, [base])

        with self.assertRaises(ValueError):
            plan_delete([unmeasured])

    def test_bin_root_for(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            local_drive = os.path.splitdrive(places.localappdata)[0]

            same_drive_path = os.path.join(local_drive, "\\some\\folder\\.venv")
            root_same = bin_root_for(same_drive_path, places)
            self.assertEqual(norm_key(root_same), norm_key(os.path.join(places.app_dir, "bin")))

            q_path = r"Q:\x\.venv"
            root_q = bin_root_for(q_path, places)
            self.assertEqual(norm_key(root_q), norm_key(r"Q:\.venvdiscover-bin"))

            # pure: nothing is created
            self.assertFalse(os.path.exists(longpath(root_same)))
            self.assertFalse(os.path.exists(longpath(root_q)))

    def test_bin_and_restore_venv(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "myenv", packages={"numpy": "2.1.0"})
            cfg_file = os.path.join(v, "pyvenv.cfg")
            with open(cfg_file, "r") as f:
                cfg_content = f.read()

            env = Env(v, Kind.VENV, "myenv", size=1000)
            now = 1793000000.0

            item = move_to_bin(env, places, now, lambda a, p, d: logs.append((a, p, d)))
            self.assertFalse(os.path.exists(longpath(v)))
            self.assertTrue(os.path.exists(longpath(item.env_path)))

            items = list_bin([item.bin_root])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].days_left(now), 14)

            restore(items[0], places, lambda a, p, d: logs.append((a, p, d)))
            self.assertTrue(os.path.exists(longpath(v)))
            with open(cfg_file, "r") as f:
                self.assertEqual(f.read(), cfg_content)

            self.assertEqual(len(list_bin([item.bin_root])), 0)

    def test_bin_conda_env_line(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            root = fakes.make_conda_root(tmp, "miniconda")
            env_path = fakes.make_conda_env(root, "py310")
            other_path = os.path.join(root, "envs", "other")
            unlisted_path = fakes.make_conda_env(root, "unlisted")

            add_env_line(places.environments_txt, env_path)
            add_env_line(places.environments_txt, other_path)

            env = Env(env_path, Kind.CONDA, "py310", size=500, conda_root=root)
            now = 1793000000.0

            item = move_to_bin(env, places, now, lambda a, p, d: logs.append((a, p, d)))
            self.assertTrue(item.had_env_line)
            with open(places.environments_txt, "r") as f:
                lines = [l.strip() for l in f if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(norm_key(lines[0]), norm_key(other_path))

            restore(item, places, lambda a, p, d: logs.append((a, p, d)))
            with open(places.environments_txt, "r") as f:
                lines = [l.strip() for l in f if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertIn(norm_key(env_path), [norm_key(l) for l in lines])

            # An env that wasn't listed doesn't get a line added
            unlisted_env = Env(unlisted_path, Kind.CONDA, "unlisted", size=500, conda_root=root)
            item_unlisted = move_to_bin(unlisted_env, places, now, lambda a, p, d: logs.append((a, p, d)))
            self.assertFalse(item_unlisted.had_env_line)
            restore(item_unlisted, places, lambda a, p, d: logs.append((a, p, d)))
            with open(places.environments_txt, "r") as f:
                lines = [l.strip() for l in f if l.strip()]
            self.assertEqual(len(lines), 2)

    def test_bin_moves_kernel(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            dirs = kernel_dirs(places)
            v = fakes.make_venv(tmp, "myenv")

            k_dir = os.path.join(dirs[0], "spec1")
            os.makedirs(k_dir, exist_ok=True)
            k_json = os.path.join(k_dir, "kernel.json")
            fakes.write_file(k_json, json.dumps({
                "argv": [os.path.join(v, "Scripts", "python.exe"), "-m", "ipykernel_launcher"],
            }))

            env = Env(v, Kind.VENV, "myenv", size=1000)
            now = 1793000000.0

            item = move_to_bin(env, places, now, lambda a, p, d: logs.append((a, p, d)))
            self.assertFalse(os.path.exists(longpath(k_dir)))
            self.assertEqual(len(item.moved), 1)
            self.assertTrue(os.path.exists(longpath(item.moved[0][1])))

            restore(item, places, lambda a, p, d: logs.append((a, p, d)))
            self.assertTrue(os.path.exists(longpath(k_dir)))
            self.assertTrue(os.path.exists(longpath(k_json)))

    def test_in_use(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "inuse_env")
            env = Env(v, Kind.VENV, "inuse_env", size=1000)
            target_file = os.path.join(v, "pyvenv.cfg")

            with open(target_file, "r") as locked_f:
                with self.assertRaises(DeleteError) as cm:
                    move_to_bin(env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
                self.assertIn("This env is in use", str(cm.exception))
                self.assertTrue(os.path.isdir(longpath(v)))
                b_root = bin_root_for(v, places)
                if os.path.exists(longpath(b_root)):
                    self.assertEqual(os.listdir(longpath(b_root)), [])

                with self.assertRaises(DeleteError) as cm2:
                    delete_permanently(env, places, lambda a, p, d: logs.append((a, p, d)))
                self.assertIn("This env is in use", str(cm2.exception))
                self.assertTrue(os.path.isdir(longpath(v)))
                if os.path.exists(longpath(b_root)):
                    self.assertEqual(os.listdir(longpath(b_root)), [])

    def test_project_root_refused(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. Project with .git, main.py, and requirements.txt (e.g. python -m venv .)
            proj_dir = os.path.join(tmp, "Projects", "myapp")
            os.makedirs(os.path.join(proj_dir, "Scripts"), exist_ok=True)
            os.makedirs(os.path.join(proj_dir, "Lib"), exist_ok=True)
            fakes.write_file(os.path.join(proj_dir, "pyvenv.cfg"), "home = C:\\Python\n")
            fakes.write_file(os.path.join(proj_dir, "main.py"), "print('hello')\n")
            fakes.write_file(os.path.join(proj_dir, "requirements.txt"), "flask==3.0\n")
            git_dir = os.path.join(proj_dir, ".git")
            os.makedirs(git_dir, exist_ok=True)
            fakes.write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main\n")

            env = Env(proj_dir, Kind.VENV, "myapp", size=5 * 1024 * 1024 * 1024)

            # move_to_bin must refuse with DeleteError and keep all files intact
            with self.assertRaises(DeleteError) as cm:
                move_to_bin(env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
            self.assertIn("This folder is also a project", str(cm.exception))
            self.assertIn("Only the env inside it could be deleted, so nothing was changed.", str(cm.exception))
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "main.py")))
            self.assertTrue(os.path.exists(os.path.join(git_dir, "HEAD")))
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "requirements.txt")))

            # delete_permanently must also refuse with DeleteError and keep all files intact
            with self.assertRaises(DeleteError) as cm2:
                delete_permanently(env, places, lambda a, p, d: logs.append((a, p, d)))
            self.assertIn("This folder is also a project", str(cm2.exception))
            self.assertIn("Only the env inside it could be deleted, so nothing was changed.", str(cm2.exception))
            self.assertTrue(os.path.exists(os.path.join(proj_dir, "main.py")))
            self.assertTrue(os.path.exists(os.path.join(git_dir, "HEAD")))

            # Test each trigger individually
            triggers = [
                (".hg", True),
                (".svn", True),
                ("pyproject.toml", False),
                ("setup.py", False),
                ("setup.cfg", False),
                ("requirements-dev.txt", False),
                ("app.py", False),
                ("analysis.ipynb", False),
            ]
            for trigger_name, is_dir in triggers:
                test_dir = os.path.join(tmp, f"proj_{trigger_name.replace('.', '_')}")
                os.makedirs(os.path.join(test_dir, "Scripts"), exist_ok=True)
                fakes.write_file(os.path.join(test_dir, "pyvenv.cfg"), "home = C:\\\n")
                trigger_path = os.path.join(test_dir, trigger_name)
                if is_dir:
                    os.makedirs(trigger_path, exist_ok=True)
                else:
                    fakes.write_file(trigger_path, "# test\n")

                t_env = Env(test_dir, Kind.VENV, "test_env", size=1000)
                with self.assertRaises(DeleteError) as cm_trig:
                    move_to_bin(t_env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
                self.assertIn("This folder is also a project", str(cm_trig.exception))
                self.assertIn(trigger_name, str(cm_trig.exception))
                self.assertTrue(os.path.exists(trigger_path))

            # 2. Standard venv with .gitignore or CACHEDIR.TAG (from virtualenv / uv) MUST NOT be refused
            normal_venv = fakes.make_venv(tmp, "normal_uv_venv")
            fakes.write_file(os.path.join(normal_venv, ".gitignore"), "*\n")
            fakes.write_file(os.path.join(normal_venv, "CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n")
            norm_env = Env(normal_venv, Kind.VENV, "normal_uv_venv", size=1000)
            item = move_to_bin(norm_env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
            self.assertFalse(os.path.exists(longpath(normal_venv)))
            self.assertTrue(os.path.exists(longpath(item.env_path)))

    def test_locked(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            root = fakes.make_conda_root(tmp, "miniconda")
            py = fakes.make_python_install(tmp, "Python313")

            base_env = Env(root, Kind.CONDA_BASE, "base", size=1000)
            py_env = Env(py, Kind.PYTHON, "Python313", size=1000)

            for e in (base_env, py_env):
                with self.assertRaises(LockedError):
                    move_to_bin(e, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
                with self.assertRaises(LockedError):
                    delete_permanently(e, places, lambda a, p, d: logs.append((a, p, d)))
                self.assertTrue(os.path.isdir(longpath(e.path)))

    def test_vanished(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            nonexistent = os.path.join(tmp, "gone_env")
            env = Env(nonexistent, Kind.VENV, "gone_env", size=1000)

            with self.assertRaises(DeleteError) as cm:
                move_to_bin(env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))
            self.assertIn("isn't there any more", str(cm.exception))

            v = fakes.make_venv(tmp, "real_env")
            real_env = Env(v, Kind.VENV, "real_env", size=1000)
            item = move_to_bin(real_env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))

            # Recreate folder at original path so restore fails
            fakes.make_venv(tmp, "real_env")
            with self.assertRaises(DeleteError) as cm2:
                restore(item, places, lambda a, p, d: logs.append((a, p, d)))
            self.assertIn("Something is already at", str(cm2.exception))
            self.assertTrue(os.path.isdir(longpath(v)))
            self.assertTrue(os.path.isdir(longpath(item.env_path)))

    def test_delete_permanently_long_path(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            deep_parent = os.path.join(tmp, *(["d" * 50] * 5))
            v = fakes.make_venv(deep_parent, "deep_env")
            self.assertGreater(len(v), 260)

            ro_file = os.path.join(v, "readonly.txt")
            fakes.write_file(ro_file, "read-only data")
            os.chmod(longpath(ro_file), stat.S_IREAD)

            env = Env(v, Kind.VENV, "deep_env", size=5000)
            delete_permanently(env, places, lambda a, p, d: logs.append((a, p, d)))

            self.assertFalse(os.path.exists(longpath(v)))
            b_root = bin_root_for(v, places)
            if os.path.exists(longpath(b_root)):
                self.assertEqual(cleanup_leftovers([b_root]), 0)

    def test_restore_rename_failure(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v = fakes.make_venv(tmp, "restore_fail_env")
            env = Env(v, Kind.VENV, "restore_fail_env", size=1000)
            item = move_to_bin(env, places, 1793000000.0, lambda a, p, d: logs.append((a, p, d)))

            with patch("os.rename", side_effect=OSError("Access denied")):
                with self.assertRaises(DeleteError) as cm:
                    restore(item, places, lambda a, p, d: logs.append((a, p, d)))
                self.assertIn("Failed to restore", str(cm.exception))

    def test_purge_expired(self):
        logs = []
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v1 = fakes.make_venv(tmp, "env1")
            v2 = fakes.make_venv(tmp, "env2")

            now = 1793000000.0
            time_15_days_ago = now - (15 * 86400)
            time_13_days_ago = now - (13 * 86400)

            item1 = move_to_bin(Env(v1, Kind.VENV, "env1", size=100), places, time_15_days_ago, lambda a, p, d: logs.append((a, p, d)))
            item2 = move_to_bin(Env(v2, Kind.VENV, "env2", size=100), places, time_13_days_ago, lambda a, p, d: logs.append((a, p, d)))

            purged = purge_expired([item1, item2], now, lambda a, p, d: logs.append((a, p, d)))
            self.assertEqual(len(purged), 1)
            self.assertEqual(purged[0].id, item1.id)

            remaining = list_bin([item1.bin_root])
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].id, item2.id)

    def test_cleanup_leftovers(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            b_root = os.path.join(places.app_dir, "bin")
            stray_dir = os.path.join(b_root, ".deleting-123-x")
            os.makedirs(longpath(stray_dir), exist_ok=True)
            fakes.write_file(os.path.join(stray_dir, "leftover.txt"), "leftover")

            cleaned = cleanup_leftovers([b_root])
            self.assertEqual(cleaned, 1)
            self.assertFalse(os.path.exists(longpath(stray_dir)))


if __name__ == "__main__":
    unittest.main()
