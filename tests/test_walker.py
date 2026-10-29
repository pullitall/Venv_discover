import os
import subprocess
import unittest

from tests import fakes
from venvdiscover.model import norm_key
from venvdiscover.walker import (
    Drive,
    _is_windows_under_drive_root,
    default_scan_drives,
    local_drives,
    walk,
)


class WalkerTests(unittest.TestCase):
    def test_default_scan_drives(self):
        drives = [
            Drive("C:\\", "NTFS", ""),
            Drive("G:\\", "FAT32", "Google Drive"),
            Drive("W:\\", "ReFS", "Developer"),
        ]
        self.assertEqual(default_scan_drives(drives), ["C:\\", "W:\\"])

    def test_local_drives_smoke(self):
        drives = local_drives()
        self.assertIsInstance(drives, list)
        self.assertTrue(any(d.root.upper() == "C:\\" for d in drives))
        for d in drives:
            self.assertTrue(d.root.endswith("\\"))
            self.assertIsInstance(d.fs, str)
            self.assertIsInstance(d.label, str)

    def test_walk_finds_all(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # 1. Plain venv
            v1 = fakes.make_venv(tmp, "v1")
            # Nested venv inside v1's folder: should not be found because v1 is not entered
            v_nested = fakes.make_venv(os.path.join(v1, "nested"), "nested_venv")

            # 2. Conda root with 2 envs
            root = fakes.make_conda_root(tmp, "conda_root")
            c1 = fakes.make_conda_env(root, "c1")
            c2 = fakes.make_conda_env(root, "c2")

            found_envs = []
            visited = walk(tmp, places, on_env=found_envs.append, should_stop=lambda: False)

            self.assertGreater(visited, 0)
            self.assertEqual(len(found_envs), 4)

            found_keys = {e.key for e in found_envs}
            self.assertIn(norm_key(v1), found_keys)
            self.assertIn(norm_key(root), found_keys)
            self.assertIn(norm_key(c1), found_keys)
            self.assertIn(norm_key(c2), found_keys)
            self.assertNotIn(norm_key(v_nested), found_keys)

    def test_walk_skips(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_good = fakes.make_venv(tmp, "good_venv")

            # Under node_modules
            nm_dir = os.path.join(tmp, "node_modules", "x")
            v_nm = fakes.make_venv(nm_dir, ".venv")

            # Under .venvdiscover-bin
            bin_dir = os.path.join(tmp, ".venvdiscover-bin", "y")
            v_bin = fakes.make_venv(bin_dir, "env")

            # Under skip_paths, passed with different case to verify case-insensitivity
            skip_dir = os.path.join(tmp, "skip_me")
            v_skip = fakes.make_venv(skip_dir, ".venv")
            skip_arg = os.path.join(tmp, "SKIP_ME")

            # Under a deep skip_path > 240 chars
            deep_skip = os.path.join(tmp, "skip_long", *(["nested_folder"] * 15))
            v_deep_skip = fakes.make_venv(deep_skip, ".venv")
            self.assertGreater(len(v_deep_skip), 240)

            found_envs = []
            walk(
                tmp,
                places,
                on_env=found_envs.append,
                should_stop=lambda: False,
                skip_paths=[skip_arg, deep_skip],
            )

            found_keys = {e.key for e in found_envs}
            self.assertIn(norm_key(v_good), found_keys)
            self.assertNotIn(norm_key(v_nm), found_keys)
            self.assertNotIn(norm_key(v_bin), found_keys)
            self.assertNotIn(norm_key(v_skip), found_keys)
            self.assertNotIn(norm_key(v_deep_skip), found_keys)

    def test_walk_long_path_plain(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            # A venv more than 260 characters deep
            deep_parent = os.path.join(tmp, "very_long_path_walk", *(["sub_directory"] * 16))
            v_deep = fakes.make_venv(deep_parent, ".venv")
            self.assertGreater(len(v_deep), 260)

            found_envs = []
            walk(tmp, places, on_env=found_envs.append, should_stop=lambda: False)

            self.assertEqual(len(found_envs), 1)
            env = found_envs[0]
            self.assertFalse(env.path.startswith("\\\\?\\"))
            self.assertEqual(env.path, v_deep)

    def test_windows_under_drive_root(self):
        self.assertTrue(_is_windows_under_drive_root(r"C:\Windows", "C:\\"))
        self.assertTrue(_is_windows_under_drive_root(r"c:\windows", "c:\\"))
        self.assertFalse(_is_windows_under_drive_root(r"D:\x\Windows", r"D:\x"))
        self.assertFalse(_is_windows_under_drive_root(r"C:\Other", "C:\\"))

        # Verify a venv under a nested Windows directory is NOT skipped
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_win = fakes.make_venv(os.path.join(tmp, "proj", "Windows"), ".venv")
            found_envs = []
            walk(tmp, places, on_env=found_envs.append, should_stop=lambda: False)
            found_keys = {e.key for e in found_envs}
            self.assertIn(norm_key(v_win), found_keys)

    def test_walk_junction(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            target = os.path.join(tmp, "target")
            os.makedirs(target, exist_ok=True)
            v_target = fakes.make_venv(target, "my_venv")

            # Create junction pointing to target
            link = os.path.join(tmp, "link_to_target")
            res1 = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
            if res1.returncode != 0:
                self.skipTest("mklink /J failed")

            # Create junction loop pointing back to parent (tmp)
            loop_link = os.path.join(target, "loop_back")
            res2 = subprocess.run(["cmd", "/c", "mklink", "/J", loop_link, tmp], capture_output=True, text=True)
            if res2.returncode != 0:
                self.skipTest("mklink /J loop failed")

            found_envs = []
            walk(tmp, places, on_env=found_envs.append, should_stop=lambda: False)

            # Target venv is reported exactly once and walk terminates without infinite loop
            self.assertEqual(len(found_envs), 1)
            self.assertEqual(norm_key(found_envs[0].path), norm_key(v_target))

    def test_walk_unreadable(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            v_good = fakes.make_venv(tmp, "good_venv")

            unreadable = os.path.join(tmp, "unreadable_folder")
            os.makedirs(unreadable, exist_ok=True)
            fakes.make_venv(unreadable, "bad_venv")

            username = os.environ.get("USERNAME", "")
            if not username:
                self.skipTest("USERNAME environment variable missing")

            res = subprocess.run(["icacls", unreadable, "/deny", f"{username}:(RX)"], capture_output=True, text=True)
            if res.returncode != 0:
                self.skipTest("icacls deny failed")

            try:
                found_envs = []
                walk(tmp, places, on_env=found_envs.append, should_stop=lambda: False)
                found_keys = {e.key for e in found_envs}
                self.assertIn(norm_key(v_good), found_keys)
            finally:
                subprocess.run(["takeown", "/f", unreadable], capture_output=True)
                subprocess.run(["icacls", unreadable, "/grant", f"{username}:F"], capture_output=True)
                subprocess.run(["icacls", unreadable, "/remove:d", username], capture_output=True)

    def test_walk_stop(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            fakes.make_venv(tmp, "v1")
            visited = walk(tmp, places, on_env=lambda e: None, should_stop=lambda: True)
            self.assertLessEqual(visited, 1)


if __name__ == "__main__":
    unittest.main()
