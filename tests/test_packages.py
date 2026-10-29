import os
import unittest

from tests import fakes
from venvdiscover.model import Env, Kind, Package, longpath
from venvdiscover.packages import fill, package_stamp, python_version, read_packages


class PackagesTests(unittest.TestCase):
    def test_venv_packages(self):
        with fakes.tmpdir() as tmp:
            v = fakes.make_venv(tmp, "my_venv", packages={"numpy": "2.1.0", "Pillow": "10.4.0"})
            env = Env(v, Kind.VENV, "my_venv")
            pkgs = read_packages(env)
            self.assertEqual(len(pkgs), 2)
            self.assertEqual(pkgs[0], Package("numpy", "2.1.0", "pip"))
            self.assertEqual(pkgs[1], Package("Pillow", "10.4.0", "pip"))

    def test_conda_mixed(self):
        with fakes.tmpdir() as tmp:
            root = fakes.make_conda_root(tmp, "conda_root")
            c_env = fakes.make_conda_env(
                root, "mixed_env", conda_packages={"numpy": "1.26.4"}, pip_packages={"requests": "2.32.3"}
            )
            sp = os.path.join(c_env, "Lib", "site-packages")
            meta = os.path.join(c_env, "conda-meta")

            # 1. Conda package whose dist-info has NO INSTALLER file -> dedup by conda package name
            fakes.write_file(os.path.join(meta, "scipy-1.11.0-h0_0.json"))
            scipy_di = os.path.join(sp, "scipy-1.11.0.dist-info")
            fakes.write_file(os.path.join(scipy_di, "METADATA"), "Name: scipy\nVersion: 1.11.0\n")

            # 2. Conda package 'pytorch' where dist-info is named 'torch' with INSTALLER 'conda'
            # -> name differs so dedup doesn't catch it; INSTALLER == 'conda' must skip it
            fakes.write_file(os.path.join(meta, "pytorch-2.4.0-cpu_0.json"))
            torch_di = os.path.join(sp, "torch-2.4.0.dist-info")
            fakes.write_file(os.path.join(torch_di, "METADATA"), "Name: torch\nVersion: 2.4.0\n")
            fakes.write_file(os.path.join(torch_di, "INSTALLER"), "conda\n")

            env = Env(c_env, Kind.CONDA, "mixed_env")
            pkgs = read_packages(env)
            pkg_map = {p.name: (p.version, p.source) for p in pkgs}

            self.assertIn("numpy", pkg_map)
            self.assertEqual(pkg_map["numpy"], ("1.26.4", "conda"))
            self.assertIn("requests", pkg_map)
            self.assertEqual(pkg_map["requests"], ("2.32.3", "pip"))

            # Ensure python package from conda-meta is also listed
            self.assertIn("python", pkg_map)
            self.assertEqual(pkg_map["python"][1], "conda")

            # Ensure scipy is listed once as conda, not duplicated as pip
            self.assertIn("scipy", pkg_map)
            self.assertEqual(pkg_map["scipy"], ("1.11.0", "conda"))
            scipy_names = [p.name for p in pkgs if p.name == "scipy"]
            self.assertEqual(len(scipy_names), 1)

            # Ensure pytorch is listed as conda and torch dist-info (INSTALLER=conda) is skipped
            self.assertIn("pytorch", pkg_map)
            self.assertEqual(pkg_map["pytorch"], ("2.4.0", "conda"))
            self.assertNotIn("torch", pkg_map)

            # Ensure no duplicate numpy
            names = [p.name for p in pkgs if p.name == "numpy"]
            self.assertEqual(len(names), 1)

    def test_conda_name_with_dashes(self):
        with fakes.tmpdir() as tmp:
            root = fakes.make_conda_root(tmp, "conda_dashes")
            c_env = fakes.make_conda_env(root, "dash_env")
            meta = os.path.join(c_env, "conda-meta")
            fakes.write_file(os.path.join(meta, "ca-certificates-2024.8.30-h56e8100_0.json"))
            env = Env(c_env, Kind.CONDA, "dash_env")
            pkgs = read_packages(env)
            ca_pkg = next((p for p in pkgs if p.name == "ca-certificates"), None)
            self.assertIsNotNone(ca_pkg)
            self.assertEqual(ca_pkg.version, "2024.8.30")
            self.assertEqual(ca_pkg.source, "conda")

    def test_versions(self):
        with fakes.tmpdir() as tmp:
            # venv with version_info
            v1 = fakes.make_venv(tmp, "v1", version="3.11.9.final.0", cfg_key="version_info")
            self.assertEqual(python_version(Env(v1, Kind.VENV, "v1")), "3.11.9")

            # venv with version
            v2 = fakes.make_venv(tmp, "v2", version="3.12.4", cfg_key="version")
            self.assertEqual(python_version(Env(v2, Kind.VENV, "v2")), "3.12.4")

            # conda env gives python json version
            root = fakes.make_conda_root(tmp, "conda_ver", python="3.10.14")
            c_env = fakes.make_conda_env(root, "c1", python="3.10.14")
            self.assertEqual(python_version(Env(c_env, Kind.CONDA, "c1")), "3.10.14")

            # python install gives 3.XX even with generic python3.dll present
            p_inst = fakes.make_python_install(tmp, "py313", version="3.13.1")
            fakes.write_file(os.path.join(p_inst, "python3.dll"))
            self.assertEqual(python_version(Env(p_inst, Kind.PYTHON, "py313")), "3.13")

    def test_broken_metadata(self):
        with fakes.tmpdir() as tmp:
            v = fakes.make_venv(tmp, "broken_venv", packages={"good": "1.0.0"})
            sp = os.path.join(v, "Lib", "site-packages")

            # 1. dist-info without METADATA
            d1 = os.path.join(sp, "foo-1.0.dist-info")
            os.makedirs(d1, exist_ok=True)

            # 2. METADATA without Version
            d2 = os.path.join(sp, "bar-2.5.dist-info")
            fakes.write_file(os.path.join(d2, "METADATA"), "Name: bar\n\nbody\n")

            # 3. METADATA with invalid UTF-8 bytes b"\xff\xfe"
            d3 = os.path.join(sp, "baz-3.0.dist-info")
            fakes.write_file(os.path.join(d3, "METADATA"), b"\xff\xfe\x00Name: baz\nVersion: 3.0\n\n")

            env = Env(v, Kind.VENV, "broken_venv")
            pkgs = read_packages(env)
            pkg_dict = {p.name: p.version for p in pkgs}

            self.assertIn("good", pkg_dict)
            self.assertEqual(pkg_dict["good"], "1.0.0")

            self.assertIn("foo", pkg_dict)
            self.assertEqual(pkg_dict["foo"], "1.0")

            self.assertIn("bar", pkg_dict)
            self.assertEqual(pkg_dict["bar"], "2.5")

    def test_unicode_long_path(self):
        with fakes.tmpdir() as tmp:
            deep_parent = os.path.join(tmp, "Kosztorysowy Pomocnik Majstra", "żabka", *([ "sub" * 10 ] * 6))
            v = fakes.make_venv(deep_parent, ".venv", packages={"scipy": "1.14.1"})
            self.assertGreater(len(v), 260)
            env = Env(v, Kind.VENV, ".venv")
            pkgs = read_packages(env)
            self.assertEqual(len(pkgs), 1)
            self.assertEqual(pkgs[0].name, "scipy")
            self.assertEqual(pkgs[0].version, "1.14.1")

    def test_stamp_changes(self):
        with fakes.tmpdir() as tmp:
            v = fakes.make_venv(tmp, "stamp_venv", packages={"click": "8.1.7"})
            env = Env(v, Kind.VENV, "stamp_venv")
            s1 = package_stamp(env)
            self.assertGreater(s1, 0.0)

            # Update site-packages mtime explicitly
            sp = os.path.join(v, "Lib", "site-packages")
            new_mtime = s1 + 1000.0
            os.utime(longpath(sp), (new_mtime, new_mtime))

            s2 = package_stamp(env)
            self.assertGreater(s2, s1)

            # Test fill sets packages, version, stamp
            env_filled = fill(env)
            self.assertEqual(env_filled.stamp, s2)
            self.assertEqual(len(env_filled.packages), 1)
            self.assertEqual(env_filled.packages[0].name, "click")


if __name__ == "__main__":
    unittest.main()
