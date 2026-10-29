import os
import unittest

from tests import fakes
from venvdiscover.model import (
    Env, Kind, Package, display_name, is_under, longpath, norm_key, normalize_name,
)
from venvdiscover.places import Places


class PlacesTests(unittest.TestCase):
    def test_places_from_environ(self):
        # Fallback: no TEMP, but TMP is set
        p1 = Places.from_environ({
            "USERPROFILE": r"C:\Users\test",
            "TMP": r"C:\CustomTmp",
            "ProgramFiles": r"C:\PF",
        })
        self.assertEqual(p1.temp, r"C:\CustomTmp")

        # Fallback: neither TEMP nor TMP set -> localappdata\Temp
        p2 = Places.from_environ({
            "USERPROFILE": r"C:\Users\test",
            "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
        })
        self.assertEqual(p2.temp, r"C:\Users\test\AppData\Local\Temp")

        # Missing ProgramFiles(x86) is skipped
        p3 = Places.from_environ({"ProgramFiles": r"C:\Program Files"})
        self.assertEqual(p3.program_files, (r"C:\Program Files",))

        # Identical ProgramFiles and ProgramFiles(x86) give a single entry
        p4 = Places.from_environ({
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files",
        })
        self.assertEqual(p4.program_files, (r"C:\Program Files",))

        # Distinct ProgramFiles and ProgramFiles(x86) are both retained in order
        p5 = Places.from_environ({
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files (x86)",
        })
        self.assertEqual(p5.program_files, (r"C:\Program Files", r"C:\Program Files (x86)"))


class EnvTests(unittest.TestCase):
    def test_env_roundtrip(self):
        e = Env(r"C:\m\envs\torch26", Kind.CONDA, "torch26", python_version="3.11.9",
                packages=[Package("numpy", "1.26.4", "conda"), Package("requests", "2.32.3", "pip")],
                size=1234, last_used=1793000000.0, conda_root=r"C:\m", stamp=5.0)
        self.assertEqual(Env.from_dict(e.to_dict()), e)

    def test_key_is_filled(self):
        e = Env(r"C:\Some\Where\.venv", Kind.VENV, "x")
        self.assertEqual(e.key, norm_key(r"C:\Some\Where\.venv"))

    def test_locked_kinds(self):
        self.assertTrue(Env(r"C:\m", Kind.CONDA_BASE, "m").locked)
        self.assertTrue(Env(r"C:\p", Kind.PYTHON, "p").locked)
        self.assertFalse(Env(r"C:\v", Kind.VENV, "v").locked)


class PathTests(unittest.TestCase):
    def test_display_name_generic(self):
        self.assertEqual(display_name(r"D:\Desktop\Zabka tricky\.venv"), r"Zabka tricky\.venv")
        self.assertEqual(display_name(r"C:\m\envs\torch26"), "torch26")
        self.assertEqual(display_name("C:\\proj\\venv\\"), r"proj\venv")

    def test_is_under(self):
        self.assertTrue(is_under(r"C:\A\b", r"c:\a"))
        self.assertTrue(is_under(r"C:\A", r"C:\A"))
        self.assertFalse(is_under(r"C:\Ab", r"C:\A"))
        self.assertTrue(is_under(r"C:\x", "C:\\"))

    def test_longpath(self):
        long = "C:\\" + "\\".join(["d" * 50] * 6)
        self.assertGreater(len(long), 260)
        self.assertTrue(longpath(long).startswith("\\\\?\\"))
        self.assertEqual(longpath(longpath(long)), longpath(long))
        self.assertEqual(longpath(r"C:\short"), r"C:\short")
        self.assertEqual(norm_key(longpath(long)), norm_key(long))

    def test_normalize_name(self):
        self.assertEqual(normalize_name("Scikit_Learn"), "scikit-learn")
        self.assertEqual(normalize_name("zope.interface"), "zope-interface")


class FakesTests(unittest.TestCase):
    def test_test_tmp_is_not_temp(self):
        parts = [p.lower() for p in fakes.TEST_TMP.split(os.sep)]
        self.assertNotIn("temp", parts)
        self.assertNotIn("tmp", parts)

    def test_fakes_build(self):
        with fakes.tmpdir() as tmp:
            v = fakes.make_venv(tmp, packages={"numpy": "2.1.0"})
            self.assertTrue(os.path.isfile(os.path.join(v, "pyvenv.cfg")))
            self.assertTrue(os.path.isfile(os.path.join(
                v, "Lib", "site-packages", "numpy-2.1.0.dist-info", "METADATA")))

            deep = os.path.join(tmp, *(["d" * 50] * 5))
            dv = fakes.make_venv(deep, packages={"six": "1.16.0"})
            self.assertGreater(len(dv), 260)
            self.assertTrue(os.path.exists(longpath(os.path.join(dv, "pyvenv.cfg"))))

    def test_fake_conda(self):
        with fakes.tmpdir() as tmp:
            root = fakes.make_conda_root(tmp, packages={"conda": "24.9.2"})
            env = fakes.make_conda_env(root, "py310", python="3.10.14",
                                       conda_packages={"numpy": "1.26.4"},
                                       pip_packages={"requests": "2.32.3"})
            meta = os.listdir(os.path.join(env, "conda-meta"))
            self.assertIn("python-3.10.14-h0_0.json", meta)
            self.assertIn("numpy-1.26.4-h0_0.json", meta)
            self.assertIn("history", meta)
            self.assertTrue(os.path.isdir(os.path.join(root, "condabin")))
            sp = os.path.join(env, "Lib", "site-packages")
            with open(os.path.join(sp, "requests-2.32.3.dist-info", "INSTALLER")) as f:
                self.assertEqual(f.read().strip(), "pip")
            with open(os.path.join(sp, "numpy-1.26.4.dist-info", "INSTALLER")) as f:
                self.assertEqual(f.read().strip(), "conda")

    def test_fake_python_install_and_places(self):
        with fakes.tmpdir() as tmp:
            p = fakes.make_python_install(tmp, version="3.13.1")
            self.assertTrue(os.path.isfile(os.path.join(p, "python313.dll")))
            self.assertTrue(os.path.isfile(os.path.join(p, "Lib", "os.py")))
            places = fakes.fake_places(tmp)
            for d in (places.home, places.appdata, places.localappdata, places.temp,
                      places.programdata, *places.program_files):
                self.assertTrue(os.path.isdir(d), d)
            self.assertEqual(places.app_dir, os.path.join(places.localappdata, "VenvDiscover"))
            self.assertEqual(places.environments_txt,
                             os.path.join(places.home, ".conda", "environments.txt"))


if __name__ == "__main__":
    unittest.main()
