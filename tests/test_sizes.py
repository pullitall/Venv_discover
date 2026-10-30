import os
import subprocess
import unittest

from tests import fakes
from venvdiscover.model import Env, Kind, longpath
from venvdiscover.sizes import LINK_CHECK_MIN, folder_size, folder_usage, last_used


class SizesTests(unittest.TestCase):
    def test_folder_usage_counts_hard_linked_files_as_shared(self):
        """Files hard-linked from outside (conda's pkgs, uv's cache) are on disk but stay when the env is deleted."""
        with fakes.tmpdir() as tmp:
            env = os.path.join(tmp, "env")
            cache = os.path.join(tmp, "pkgs")
            own, shared, small, inner = 8192, 16384, 100, 8192
            self.assertLess(small, LINK_CHECK_MIN)
            self.assertGreaterEqual(min(own, shared, inner), LINK_CHECK_MIN)

            fakes.write_file(os.path.join(env, "own.bin"), b"o" * own)
            fakes.write_file(os.path.join(cache, "big.bin"), b"s" * shared)
            os.makedirs(os.path.join(env, "Lib"), exist_ok=True)
            os.link(os.path.join(cache, "big.bin"), os.path.join(env, "Lib", "big.bin"))
            # Below LINK_CHECK_MIN the link count isn't read: a small linked file counts as the env's own
            fakes.write_file(os.path.join(cache, "tiny.txt"), b"t" * small)
            os.link(os.path.join(cache, "tiny.txt"), os.path.join(env, "tiny.txt"))
            # One file linked twice inside the env is freed once
            fakes.write_file(os.path.join(env, "a.bin"), b"i" * inner)
            os.link(os.path.join(env, "a.bin"), os.path.join(env, "Lib", "a_again.bin"))

            usage = folder_usage(env)
            self.assertEqual(usage.total, own + shared + small + 2 * inner)
            self.assertEqual(usage.unique, own + small + inner)
            self.assertEqual(folder_size(env), usage.total)
            self.assertEqual(folder_usage(env, check_links=False).unique, usage.total)

            # With the cache copy gone, the env holds the only link: deleting it frees that file too
            os.remove(os.path.join(cache, "big.bin"))
            self.assertEqual(folder_usage(env).unique, own + small + inner + shared)

    def test_folder_size(self):
        with fakes.tmpdir() as tmp:
            d = os.path.join(tmp, "size_test")
            os.makedirs(d, exist_ok=True)
            fakes.write_file(os.path.join(d, "f1.txt"), b"x" * 10)
            fakes.write_file(os.path.join(d, "sub", "f2.txt"), b"y" * 20)
            fakes.write_file(os.path.join(d, "sub", "deep", "f3.txt"), b"z" * 30)

            # File in a > 260-character subpath (Review Focus 1)
            deep_long = os.path.join(d, *(["nested" * 10] * 5))
            fakes.write_file(os.path.join(deep_long, "long.bin"), b"w" * 40)
            self.assertGreater(len(os.path.join(deep_long, "long.bin")), 260)

            total = folder_size(d)
            self.assertEqual(total, 10 + 20 + 30 + 40)

            # Create a junction inside d pointing to outside folder with 1000 bytes
            outside = os.path.join(tmp, "outside_dir")
            os.makedirs(outside, exist_ok=True)
            fakes.write_file(os.path.join(outside, "outside_file.bin"), b"a" * 1000)

            j_link = os.path.join(d, "junction_link")
            res = subprocess.run(["cmd", "/c", "mklink", "/J", j_link, outside], capture_output=True, text=True)
            if res.returncode != 0:
                self.skipTest("mklink /J failed")
            # Junction must not be traversed into, total size unchanged
            total_with_j = folder_size(d)
            self.assertEqual(total_with_j, 10 + 20 + 30 + 40)

            # Stop straight away returns small or 0
            stopped = folder_size(d, should_stop=lambda: True)
            self.assertEqual(stopped, 0)

    def test_folder_size_skip_dirs(self):
        with fakes.tmpdir() as tmp:
            base = fakes.make_conda_root(tmp, "miniconda_base")
            # Put 1000 bytes inside base\envs\myenv
            env_file = os.path.join(base, "envs", "myenv", "large.bin")
            fakes.write_file(env_file, b"x" * 1000)

            # Measuring with skip_dirs=("envs",) ignores the 1000 bytes
            size_without_envs = folder_size(base, skip_dirs=("envs",))
            size_with_envs = folder_size(base)

            self.assertEqual(size_with_envs - size_without_envs, 1000)

            # Deeper Lib\envs\f.bin inside fake base IS counted (skip_dirs only applies at top level)
            deep_file = os.path.join(base, "Lib", "envs", "f.bin")
            fakes.write_file(deep_file, b"y" * 250)
            size_with_deep = folder_size(base, skip_dirs=("envs",))
            self.assertEqual(size_with_deep - size_without_envs, 250)

    def test_last_used(self):
        with fakes.tmpdir() as tmp:
            v = fakes.make_venv(tmp, "my_venv")
            env = Env(v, Kind.VENV, "my_venv")

            # Set distinct timestamps
            t_base = 1793000000.0
            sp = os.path.join(v, "Lib", "site-packages")
            cfg = os.path.join(v, "pyvenv.cfg")
            py_exe = os.path.join(v, "Scripts", "python.exe")

            os.utime(longpath(sp), (t_base + 100, t_base + 100))
            os.utime(longpath(cfg), (t_base + 200, t_base + 200))
            os.utime(longpath(py_exe), (t_base + 300, t_base + 50))  # atime is t_base + 300

            lu = last_used(env)
            self.assertIsNotNone(lu)
            self.assertEqual(lu, t_base + 300)

            # An empty folder gives None
            empty_dir = os.path.join(tmp, "empty_env")
            os.makedirs(empty_dir, exist_ok=True)
            empty_env = Env(empty_dir, Kind.VENV, "empty_env")
            self.assertIsNone(last_used(empty_env))


if __name__ == "__main__":
    unittest.main()
