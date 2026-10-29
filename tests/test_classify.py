import os
import unittest

from tests import fakes
from venvdiscover.classify import probe, python_install
from venvdiscover.model import Kind


class ClassifyTests(unittest.TestCase):
    def test_conda_root_and_env(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            root = fakes.make_conda_root(tmp, "miniconda3")
            env = fakes.make_conda_env(root, "py310")

            root_e = probe(root, places)
            self.assertIsNotNone(root_e)
            self.assertEqual(root_e.kind, Kind.CONDA_BASE)
            self.assertEqual(root_e.conda_root, root)

            env_e = probe(env, places)
            self.assertIsNotNone(env_e)
            self.assertEqual(env_e.kind, Kind.CONDA)
            self.assertEqual(env_e.conda_root, root)

            # Conda base with envs/ only (no condabin/)
            base_envs_only = os.path.join(tmp, "base_envs_only")
            fakes.write_file(os.path.join(base_envs_only, "conda-meta", "history"), "")
            os.makedirs(os.path.join(base_envs_only, "envs"), exist_ok=True)
            e = probe(base_envs_only, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.CONDA_BASE)
            self.assertEqual(e.conda_root, base_envs_only)

            # Conda base with condabin/ only (no envs/)
            base_condabin_only = os.path.join(tmp, "base_condabin_only")
            fakes.write_file(os.path.join(base_condabin_only, "conda-meta", "history"), "")
            os.makedirs(os.path.join(base_condabin_only, "condabin"), exist_ok=True)
            e = probe(base_condabin_only, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.CONDA_BASE)
            self.assertEqual(e.conda_root, base_condabin_only)

    def test_conda_env_outside_root(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            custom_env = os.path.join(places.home, ".conda", "envs", "custom")
            meta = os.path.join(custom_env, "conda-meta")
            fakes.write_file(os.path.join(meta, "history"), "==> 2026-10-26 <==\n")

            env_e = probe(custom_env, places)
            self.assertIsNotNone(env_e)
            self.assertEqual(env_e.kind, Kind.CONDA)
            self.assertIsNone(env_e.conda_root)

    def test_venv_kinds(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            # home\proj\.venv -> VENV
            proj_venv = fakes.make_venv(os.path.join(places.home, "proj"), ".venv")
            e = probe(proj_venv, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.VENV)

            # localappdata\Temp\b\.build-venv -> TEMP_VENV
            temp_venv = fakes.make_venv(os.path.join(places.localappdata, "Temp", "b"), ".build-venv")
            e = probe(temp_venv, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.TEMP_VENV)

            # appdata\Gw2Launcher\data\1\AppData\Local\Temp\x\.build-venv -> TEMP_VENV
            gw2_venv = fakes.make_venv(
                os.path.join(places.appdata, "Gw2Launcher", "data", "1", "AppData", "Local", "Temp", "x"),
                ".build-venv",
            )
            e = probe(gw2_venv, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.TEMP_VENV)

            # home\.lmstudio\ext\v1 -> APP_VENV
            lm_venv = fakes.make_venv(os.path.join(places.home, ".lmstudio", "ext"), "v1")
            e = probe(lm_venv, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.APP_VENV)

            # localappdata\pipx\pipx\venvs\black -> APP_VENV
            pipx_venv = fakes.make_venv(
                os.path.join(places.localappdata, "pipx", "pipx", "venvs"), "black"
            )
            e = probe(pipx_venv, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.APP_VENV)

            # home\env -> VENV
            home_env = fakes.make_venv(places.home, "env")
            e = probe(home_env, places)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.VENV)

    def test_not_env(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            empty_dir = os.path.join(tmp, "empty")
            os.makedirs(empty_dir, exist_ok=True)
            self.assertIsNone(probe(empty_dir, places))
            self.assertIsNone(python_install(empty_dir))

    def test_python_install(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            py_inst = fakes.make_python_install(tmp, "Python312", version="3.12.4")
            e = python_install(py_inst)
            self.assertIsNotNone(e)
            self.assertEqual(e.kind, Kind.PYTHON)

            # A venv folder gives None for python_install
            venv = fakes.make_venv(tmp, "some_venv")
            self.assertIsNone(python_install(venv))

            # Conda base with python.exe and Lib\os.py gives None (not duplicate python install)
            root = fakes.make_conda_root(tmp, "conda_for_py_install")
            fakes.write_file(os.path.join(root, "Lib", "os.py"), "# os\n")
            self.assertIsNone(python_install(root))

            # Folder with python.exe, Lib\os.py and pyvenv.cfg gives None
            venv_with_root_py = os.path.join(tmp, "venv_with_root_py")
            fakes.write_file(os.path.join(venv_with_root_py, "python.exe"))
            fakes.write_file(os.path.join(venv_with_root_py, "Lib", "os.py"), "# os\n")
            fakes.write_file(os.path.join(venv_with_root_py, "pyvenv.cfg"), "home = C:\\\n")
            self.assertIsNone(python_install(venv_with_root_py))


if __name__ == "__main__":
    unittest.main()
