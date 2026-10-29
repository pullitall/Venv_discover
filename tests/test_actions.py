import os
import unittest
from unittest.mock import patch

from tests import fakes
from venvdiscover.actions import (
    activate_command,
    conda_exe,
    open_folder,
    run_conda_clean,
)
from venvdiscover.model import Env, Kind


class ActionsTests(unittest.TestCase):
    def test_activate_command(self):
        # 1. CONDA_BASE: "conda activate base"
        base_env = Env(r"C:\miniconda", Kind.CONDA_BASE, "base")
        self.assertEqual(activate_command(base_env), "conda activate base")

        # 2. CONDA under its root's envs\: f"conda activate {basename}"
        root = r"C:\miniconda"
        named_conda = Env(r"C:\miniconda\envs\myenv", Kind.CONDA, "myenv", conda_root=root)
        self.assertEqual(activate_command(named_conda), "conda activate myenv")

        # 2b. Generic env name under envs\ (display_name would be "envs\\env") -> must use basename "env"
        generic_named_conda = Env(r"C:\miniconda\envs\env", Kind.CONDA, r"envs\env", conda_root=root)
        self.assertEqual(activate_command(generic_named_conda), "conda activate env")

        # 3. other CONDA: f'conda activate "{env.path}"'
        path_conda1 = Env(r"D:\other\custom_env", Kind.CONDA, "custom_env", conda_root=None)
        self.assertEqual(activate_command(path_conda1), 'conda activate "D:\\other\\custom_env"')

        path_conda2 = Env(r"D:\other\custom_env", Kind.CONDA, "custom_env", conda_root=root)
        self.assertEqual(activate_command(path_conda2), 'conda activate "D:\\other\\custom_env"')

        # 4. venv kinds: f'"{env.path}\\Scripts\\activate"'
        plain_venv = Env(r"C:\project\.venv", Kind.VENV, ".venv")
        self.assertEqual(activate_command(plain_venv), '"C:\\project\\.venv\\Scripts\\activate"')

        app_venv = Env(r"C:\Users\User\.app\env", Kind.APP_VENV, "env")
        self.assertEqual(activate_command(app_venv), '"C:\\Users\\User\\.app\\env\\Scripts\\activate"')

        temp_venv = Env(r"C:\Temp\.build-venv", Kind.TEMP_VENV, ".build-venv")
        self.assertEqual(activate_command(temp_venv), '"C:\\Temp\\.build-venv\\Scripts\\activate"')

        # 5. PYTHON: ""
        py_install = Env(r"C:\Python312", Kind.PYTHON, "Python312")
        self.assertEqual(activate_command(py_install), "")

    def test_conda_exe(self):
        with fakes.tmpdir() as tmp:
            root = os.path.join(tmp, "miniconda")
            scripts_dir = os.path.join(root, "Scripts")
            condabin_dir = os.path.join(root, "condabin")
            os.makedirs(scripts_dir, exist_ok=True)
            os.makedirs(condabin_dir, exist_ok=True)

            # Neither exists
            self.assertIsNone(conda_exe(root))

            # Only condabin\conda.bat exists
            bat_path = os.path.join(condabin_dir, "conda.bat")
            fakes.write_file(bat_path, "@echo off\n")
            self.assertEqual(conda_exe(root), bat_path)

            # Scripts\conda.exe also exists -> Scripts preferred
            exe_path = os.path.join(scripts_dir, "conda.exe")
            fakes.write_file(exe_path, "binary")
            self.assertEqual(conda_exe(root), exe_path)

    def test_run_conda_clean_real_process(self):
        with fakes.tmpdir() as tmp:
            bat_file = os.path.join(tmp, "fake_clean.bat")
            # Echo two stdout lines, one stderr line, and exit with code 3
            fakes.write_file(
                bat_file,
                "@echo line one\r\n@echo oops 1>&2\r\n@echo line two\r\n@exit /b 3\r\n"
            )

            lines = []
            exit_code = run_conda_clean(bat_file, lambda line: lines.append(line.strip()))

            self.assertEqual(set(lines), {"line one", "oops", "line two"})
            self.assertEqual(len(lines), 3)
            self.assertEqual(exit_code, 3)

    def test_open_folder(self):
        with patch("os.startfile", create=True) as mock_startfile:
            open_folder(r"C:\some\folder")
            mock_startfile.assert_called_once_with(r"C:\some\folder")


if __name__ == "__main__":
    unittest.main()
