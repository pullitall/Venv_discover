"""Unit tests for terminal detection, start directory, activation commands, and launch."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import fakes
from venvdiscover.model import Env, Kind, norm_key
from venvdiscover.terminal import (
    Terminal,
    available_terminals,
    open_terminal,
    start_dir,
    terminal_command,
)


class TerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Create a single real venv for test 6 so tests run fast.
        # Path contains space, single quote, Polish letters, and (2) as requested.
        cls.real_tmp = fakes.tmpdir()
        cls.real_venv_path = os.path.join(
            cls.real_tmp.name, "term probe", "Zażółć O'Brien (2)", "my venv"
        )
        os.makedirs(os.path.dirname(cls.real_venv_path), exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", cls.real_venv_path], check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.real_tmp.cleanup()

    def setUp(self) -> None:
        patcher = patch("venvdiscover.terminal.registry_install_paths", return_value=[])
        self.mock_registry = patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_dir(self) -> None:
        """Start dir is env folder by default, or existing .project target, or env folder if target missing."""
        with fakes.tmpdir() as tmp:
            env_path = os.path.join(tmp, "my_env")
            os.makedirs(env_path, exist_ok=True)
            env = Env(env_path, Kind.VENV, "my_env")

            # 1. Default: env folder
            self.assertEqual(start_dir(env), env_path)

            # 2. .project naming an existing folder
            proj_dir = os.path.join(tmp, "my_project")
            os.makedirs(proj_dir, exist_ok=True)
            project_file = os.path.join(env_path, ".project")
            fakes.write_file(project_file, f"{proj_dir}\n")
            self.assertEqual(start_dir(env), proj_dir)

            # 3. .project naming a missing folder -> fallback to env folder
            missing_dir = os.path.join(tmp, "nonexistent_project_folder")
            fakes.write_file(project_file, f"{missing_dir}\n")
            self.assertEqual(start_dir(env), env_path)

    def test_available_terminals(self) -> None:
        """Terminal detection with fake environ mapping; missing exes are not listed."""
        with fakes.tmpdir() as tmp:
            fake_bin = os.path.join(tmp, "bin")
            fake_pf = os.path.join(tmp, "Program Files")
            fake_local = os.path.join(tmp, "LocalAppData")
            fake_sys = os.path.join(tmp, "Windows")
            for d in (fake_bin, fake_pf, fake_local, fake_sys):
                os.makedirs(d, exist_ok=True)

            fake_cmd = os.path.join(fake_sys, "System32", "cmd.exe")
            fake_ps = os.path.join(fake_sys, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            fakes.write_file(fake_cmd, "")
            fakes.write_file(fake_ps, "")

            fake_env = {
                "PATH": fake_bin,
                "COMSPEC": fake_cmd,
                "SystemRoot": fake_sys,
                "ProgramFiles": fake_pf,
                "LOCALAPPDATA": fake_local,
            }

            # With no optional terminals present, only cmd and powershell are found
            terms = available_terminals(fake_env)
            ids = [t.id for t in terms]
            self.assertEqual(ids, ["cmd", "powershell"])
            self.assertEqual(terms[0].exe, fake_cmd)
            self.assertEqual(terms[1].exe, fake_ps)

            # Add pwsh under ProgramFiles\PowerShell\7\pwsh.exe
            pwsh_exe = os.path.join(fake_pf, "PowerShell", "7", "pwsh.exe")
            fakes.write_file(pwsh_exe, "")
            terms2 = available_terminals(fake_env)
            ids2 = [t.id for t in terms2]
            self.assertIn("pwsh", ids2)

            # Add wt under LOCALAPPDATA\Microsoft\WindowsApps\wt.exe
            wt_exe = os.path.join(fake_local, "Microsoft", "WindowsApps", "wt.exe")
            fakes.write_file(wt_exe, "")
            terms3 = available_terminals(fake_env)
            ids3 = [t.id for t in terms3]
            self.assertIn("wt-powershell", ids3)
            self.assertIn("wt-cmd", ids3)

            # Add Git Bash under ProgramFiles\Git\bin\bash.exe
            git_exe = os.path.join(fake_pf, "Git", "bin", "bash.exe")
            fakes.write_file(git_exe, "")
            terms4 = available_terminals(fake_env)
            ids4 = [t.id for t in terms4]
            self.assertIn("gitbash", ids4)

    def test_gitbash_rcfile(self) -> None:
        """The rcfile lands under places.app_dir/terminals/, has stable name, and escapes single quotes."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tricky_dir = os.path.join(tmp, "O'Brien (2)")
            os.makedirs(tricky_dir, exist_ok=True)
            env_path = fakes.make_venv(tricky_dir, "bash_venv")
            env = Env(env_path, Kind.VENV, "bash_venv")

            t = Terminal("gitbash", "Git Bash", r"C:\Program Files\Git\bin\bash.exe")
            cmd, cwd, child_env = terminal_command(env, t, places)

            terminals_dir = os.path.join(places.app_dir, "terminals")
            self.assertTrue(os.path.isdir(terminals_dir))

            self.assertIsInstance(cmd, list)
            rcfile = cmd[2]
            self.assertTrue(os.path.isfile(rcfile))
            self.assertTrue(rcfile.startswith(terminals_dir))
            self.assertEqual(cmd, [t.exe, "--rcfile", rcfile, "-i"])
            self.assertIsNone(child_env)

            with open(rcfile, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn(". ~/.bashrc", content)
            act_posix = os.path.join(env_path, "Scripts", "activate").replace("\\", "/").replace("'", r"'\''")
            self.assertIn(f". '{act_posix}'", content)

            # Check conda rcfile quoting with single quotes and ' escaped as '\''
            conda_root = os.path.join(tmp, "conda O'Brien (2)")
            fakes.make_conda_root(tmp, "conda O'Brien (2)")
            conda_env = Env(
                os.path.join(conda_root, "envs", "my'env"),
                Kind.CONDA,
                "my'env",
                conda_root=conda_root,
            )
            cmd_c, _, _ = terminal_command(conda_env, t, places)
            rc_conda = cmd_c[2]
            with open(rc_conda, "r", encoding="utf-8") as f:
                c_content = f.read()

            root_posix = conda_root.replace("\\", "/").replace("'", r"'\''")
            self.assertIn(f"eval \"$('{root_posix}/Scripts/conda.exe' shell.bash hook)\"", c_content)
            self.assertIn("conda activate 'my'\\''env'", c_content)

            # If real Git Bash is installed, run bash -n on both to verify valid syntax
            git_real = r"C:\Program Files\Git\bin\bash.exe"
            if os.path.isfile(git_real):
                res_v = subprocess.run([git_real, "-n", rcfile], capture_output=True, text=True)
                self.assertEqual(res_v.returncode, 0, f"bash -n failed for venv rc: {res_v.stderr}")
                res_c = subprocess.run([git_real, "-n", rc_conda], capture_output=True, text=True)
                self.assertEqual(res_c.returncode, 0, f"bash -n failed for conda rc: {res_c.stderr}")

            # Calling again uses the same stable path and overwrites
            cmd2, _, _ = terminal_command(env, t, places)
            self.assertEqual(cmd2[2], rcfile)

    def test_commands_per_kind(self) -> None:
        """Exact command for each env kind × each terminal id, including path with space, Polish letters and '."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            tricky_name = "O'Connor Zabka żabka"
            tricky_path = os.path.join(tmp, "Zażółć gęślą jaźń", tricky_name, ".venv")
            os.makedirs(tricky_path, exist_ok=True)

            conda_root = os.path.join(tmp, "conda_root")
            os.makedirs(os.path.join(conda_root, "condabin"), exist_ok=True)
            os.makedirs(os.path.join(conda_root, "shell", "condabin"), exist_ok=True)

            py_install = os.path.join(tmp, "Python313")
            os.makedirs(py_install, exist_ok=True)

            envs = [
                Env(tricky_path, Kind.VENV, tricky_name),
                Env(tricky_path, Kind.APP_VENV, tricky_name),
                Env(tricky_path, Kind.TEMP_VENV, tricky_name),
                Env(conda_root, Kind.CONDA_BASE, "base", conda_root=conda_root),
                Env(os.path.join(conda_root, "envs", "my_env"), Kind.CONDA, "my_env", conda_root=conda_root),
                Env(py_install, Kind.PYTHON, "Python313"),
            ]

            terminals = [
                Terminal("cmd", "Command Prompt", "cmd.exe"),
                Terminal("powershell", "Windows PowerShell", "powershell.exe"),
                Terminal("pwsh", "PowerShell 7", "pwsh.exe"),
                Terminal("wt-cmd", "Windows Terminal (Command Prompt)", "wt.exe"),
                Terminal("wt-powershell", "Windows Terminal (PowerShell)", "wt.exe"),
                Terminal("gitbash", "Git Bash", "bash.exe"),
            ]

            for env in envs:
                for term in terminals:
                    cmd, cwd, child_env = terminal_command(env, term, places, environ={"PATH": "orig_path"})

                    self.assertEqual(cwd, env.path)

                    if env.kind == Kind.PYTHON:
                        self.assertIsNotNone(child_env)
                        self.assertTrue(child_env["PATH"].startswith(f"{env.path};{os.path.join(env.path, 'Scripts')}"))
                    else:
                        self.assertIsNone(child_env)

                    # 1. cmd returns a string
                    if term.id == "cmd":
                        self.assertIsInstance(cmd, str)
                        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
                            act_bat = os.path.join(env.path, "Scripts", "activate.bat")
                            self.assertEqual(cmd, f'"{term.exe}" /k call "{act_bat}"')
                        elif env.kind == Kind.CONDA_BASE:
                            bat = os.path.join(conda_root, "condabin", "conda.bat")
                            self.assertEqual(cmd, f'"{term.exe}" /k call "{bat}" activate "base"')
                        elif env.kind == Kind.CONDA:
                            bat = os.path.join(conda_root, "condabin", "conda.bat")
                            self.assertEqual(cmd, f'"{term.exe}" /k call "{bat}" activate "my_env"')
                        elif env.kind == Kind.PYTHON:
                            self.assertEqual(cmd, f'"{term.exe}"')

                    # 2. wt-cmd returns a string
                    elif term.id == "wt-cmd":
                        self.assertIsInstance(cmd, str)
                        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
                            act_bat = os.path.join(env.path, "Scripts", "activate.bat")
                            self.assertEqual(cmd, f'"{term.exe}" -d "{cwd}" cmd.exe /k call "{act_bat}"')
                        elif env.kind == Kind.CONDA_BASE:
                            bat = os.path.join(conda_root, "condabin", "conda.bat")
                            self.assertEqual(cmd, f'"{term.exe}" -d "{cwd}" cmd.exe /k call "{bat}" activate "base"')
                        elif env.kind == Kind.CONDA:
                            bat = os.path.join(conda_root, "condabin", "conda.bat")
                            self.assertEqual(cmd, f'"{term.exe}" -d "{cwd}" cmd.exe /k call "{bat}" activate "my_env"')
                        elif env.kind == Kind.PYTHON:
                            self.assertEqual(cmd, f'"{term.exe}" -d "{cwd}" cmd.exe')

                    # 3. powershell and pwsh return list[str]
                    elif term.id in ("powershell", "pwsh"):
                        self.assertIsInstance(cmd, list)
                        if env.kind in (Kind.VENV, Kind.APP_VENV, Kind.TEMP_VENV):
                            act_ps1 = os.path.join(env.path, "Scripts", "Activate.ps1").replace("'", "''")
                            self.assertEqual(cmd, [term.exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f"& '{act_ps1}'"])
                        elif env.kind == Kind.CONDA_BASE:
                            hook = os.path.join(conda_root, "shell", "condabin", "conda-hook.ps1").replace("'", "''")
                            self.assertEqual(cmd, [term.exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f"& '{hook}'; conda activate 'base'"])
                        elif env.kind == Kind.CONDA:
                            hook = os.path.join(conda_root, "shell", "condabin", "conda-hook.ps1").replace("'", "''")
                            self.assertEqual(cmd, [term.exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f"& '{hook}'; conda activate 'my_env'"])
                        elif env.kind == Kind.PYTHON:
                            self.assertEqual(cmd, [term.exe, "-NoExit"])

                    # 4. wt-powershell returns list[str]
                    elif term.id == "wt-powershell":
                        self.assertIsInstance(cmd, list)
                        self.assertEqual(cmd[0], "wt.exe")
                        self.assertEqual(cmd[1], "-d")
                        self.assertEqual(cmd[2], env.path)
                        self.assertEqual(cmd[3], "powershell.exe")

                    # 5. gitbash returns list[str]
                    elif term.id == "gitbash":
                        self.assertIsInstance(cmd, list)
                        self.assertEqual(cmd[0], "bash.exe")
                        self.assertEqual(cmd[1], "--rcfile")
                        self.assertEqual(cmd[3], "-i")

    def test_conda_env_root_resolution_and_fallback(self) -> None:
        """Test conda env with no root attribute: resolves via find_conda_roots, or falls back to PATH."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            cmd_term = Terminal("cmd", "Command Prompt", "cmd.exe")
            ps_term = Terminal("powershell", "Windows PowerShell", "powershell.exe")
            wt_cmd_term = Terminal("wt-cmd", "Windows Terminal (Command Prompt)", "wt.exe")

            # Case A: env has no conda_root, but find_conda_roots finds a root in environ
            conda_root = fakes.make_conda_root(tmp, "discovered_base")
            fakes.write_file(places.environments_txt, f"{conda_root}\n")

            orphan_env_path = os.path.join(tmp, "custom_orphaned_env")
            os.makedirs(orphan_env_path, exist_ok=True)
            orphan_env = Env(orphan_env_path, Kind.CONDA, "custom_orphaned_env", conda_root=None)

            fake_env = {"CONDA_EXE": os.path.join(conda_root, "Scripts", "conda.exe")}
            cmd_a, _, child_env_a = terminal_command(orphan_env, cmd_term, places, environ=fake_env)
            self.assertIsNone(child_env_a)
            # Activates by full path using discovered base conda.bat
            expected_bat = os.path.join(conda_root, "condabin", "conda.bat")
            self.assertEqual(cmd_a, f'"{cmd_term.exe}" /k call "{expected_bat}" activate "{orphan_env_path}"')

            # Case B: no root found anywhere
            empty_places = fakes.fake_places(os.path.join(tmp, "empty_places"))
            cmd_b, _, child_env_b = terminal_command(orphan_env, cmd_term, empty_places, environ={})
            # Activates nothing, prefixes PATH with env;env\Library\bin;env\Scripts
            self.assertEqual(cmd_b, f'"{cmd_term.exe}"')
            self.assertIsNotNone(child_env_b)
            lib_bin = os.path.join(orphan_env_path, "Library", "bin")
            scripts = os.path.join(orphan_env_path, "Scripts")
            self.assertTrue(child_env_b["PATH"].startswith(f"{orphan_env_path};{lib_bin};{scripts}"))

            # PowerShell with no root
            cmd_ps_b, _, _ = terminal_command(orphan_env, ps_term, empty_places, environ={})
            self.assertEqual(cmd_ps_b, [ps_term.exe, "-NoExit"])

            # wt-cmd with no root
            cmd_wt_b, _, _ = terminal_command(orphan_env, wt_cmd_term, empty_places, environ={})
            self.assertEqual(cmd_wt_b, f'"{wt_cmd_term.exe}" -d "{orphan_env_path}" cmd.exe')

    def test_wt_escapes_semicolons(self) -> None:
        """Windows Terminal commands never contain bare semicolons (escaped as \\;)."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            conda_root = os.path.join(tmp, "conda_root")
            os.makedirs(os.path.join(conda_root, "condabin"), exist_ok=True)
            os.makedirs(os.path.join(conda_root, "shell", "condabin"), exist_ok=True)

            env = Env(os.path.join(conda_root, "envs", "test_env"), Kind.CONDA, "test_env", conda_root=conda_root)

            # 1. wt-powershell (list)
            wt_ps = Terminal("wt-powershell", "Windows Terminal (PowerShell)", "wt.exe")
            cmd_ps, cwd, _ = terminal_command(env, wt_ps, places)
            self.assertIsInstance(cmd_ps, list)
            for arg in cmd_ps:
                unescaped = re.findall(r"(?<!\\);", arg)
                self.assertEqual(unescaped, [], f"Bare semicolon in wt-powershell: {arg}")
            self.assertIn(r"\;", cmd_ps[-1])

            # 2. wt-cmd (string)
            wt_cmd = Terminal("wt-cmd", "Windows Terminal (Command Prompt)", "wt.exe")
            cmd_c, _, _ = terminal_command(env, wt_cmd, places)
            self.assertIsInstance(cmd_c, str)
            unescaped_c = re.findall(r"(?<!\\);", cmd_c)
            self.assertEqual(unescaped_c, [], f"Bare semicolon in wt-cmd: {cmd_c}")

            # 3. wt-cmd with venv path containing ';'
            semi_path = os.path.join(tmp, "a;b", "my_env")
            os.makedirs(os.path.join(semi_path, "Scripts"), exist_ok=True)
            env_semi = Env(semi_path, Kind.VENV, "my_env")
            cmd_semi, _, _ = terminal_command(env_semi, wt_cmd, places)
            self.assertIsInstance(cmd_semi, str)
            unescaped_semi = re.findall(r"(?<!\\);", cmd_semi)
            self.assertEqual(unescaped_semi, [], f"Bare semicolon in wt-cmd with semicolon in path: {cmd_semi}")
            self.assertIn(r"\;", cmd_semi)

    def test_wt_dir_trailing_backslash(self) -> None:
        """Windows Terminal -d strips trailing backslash, or doubles it on drive root."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            target_dir = os.path.join(tmp, "my_project")
            os.makedirs(target_dir, exist_ok=True)

            env_path = os.path.join(tmp, "env_with_project")
            os.makedirs(env_path, exist_ok=True)
            project_file = os.path.join(env_path, ".project")
            fakes.write_file(project_file, target_dir + "\\\n")

            env = Env(env_path, Kind.VENV, "env_with_project")
            self.assertEqual(start_dir(env), target_dir + "\\")

            # wt-cmd: -d takes target_dir without trailing backslash
            wt_cmd = Terminal("wt-cmd", "Windows Terminal (Command Prompt)", "wt.exe")
            cmd_c, _, _ = terminal_command(env, wt_cmd, places)
            self.assertIsInstance(cmd_c, str)
            self.assertIn(f'-d "{target_dir}"', cmd_c)
            self.assertNotIn(f'-d "{target_dir}\\"', cmd_c)

            # wt-powershell: -d takes target_dir without trailing backslash
            wt_ps = Terminal("wt-powershell", "Windows Terminal (PowerShell)", "wt.exe")
            cmd_ps, _, _ = terminal_command(env, wt_ps, places)
            self.assertIsInstance(cmd_ps, list)
            idx = cmd_ps.index("-d")
            self.assertEqual(cmd_ps[idx + 1], target_dir)

            # Drive root: C:\ gives C:\\
            from venvdiscover.terminal import _format_wt_dir
            self.assertEqual(_format_wt_dir("C:\\"), "C:\\\\")
            self.assertEqual(_format_wt_dir("C:"), "C:\\\\")
            self.assertEqual(_format_wt_dir("D:\\proj\\"), "D:\\proj")

    def test_real_process_cmd_and_powershell(self) -> None:
        """Run real cmd and powershell processes and verify activation and redirect precedence."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            venv_path = self.real_venv_path
            env = Env(venv_path, Kind.VENV, "my venv")

            # 1. Real CMD test
            cmd_term = Terminal("cmd", "Command Prompt", "cmd.exe")
            cmd_cmd, cwd_cmd, _ = terminal_command(env, cmd_term, places)
            self.assertIsInstance(cmd_cmd, str)

            test_py = 'python -c "import sys,os; print(sys.prefix); print(os.getcwd())"'
            # Exact command line from terminal_command, only changing /k to /c and appending check
            cmd_run = cmd_cmd.replace(" /k ", " /c ", 1) + f" && {test_py}"

            proc_env = dict(os.environ, PYTHONIOENCODING="utf-8")
            res = subprocess.run(
                cmd_run,
                cwd=cwd_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=proc_env,
                check=True,
            )
            lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2)
            prefix = lines[-2]
            curr_dir = lines[-1]

            self.assertEqual(norm_key(prefix), norm_key(venv_path))
            self.assertEqual(norm_key(curr_dir), norm_key(venv_path))

            # Add redirect: cd /d <target_dir> at the end of activate.bat
            redirect_target = os.path.join(tmp, "custom_redirect_target")
            os.makedirs(redirect_target, exist_ok=True)
            act_bat = os.path.join(venv_path, "Scripts", "activate.bat")

            with open(act_bat, "r", encoding="utf-8", errors="replace") as f:
                orig_bat = f.read()

            try:
                with open(act_bat, "a", encoding="utf-8") as f:
                    f.write(f'\ncd /d "{redirect_target}"\n')

                res2 = subprocess.run(
                    cmd_run,
                    cwd=cwd_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=proc_env,
                    check=True,
                )
                lines2 = [line.strip() for line in res2.stdout.strip().splitlines() if line.strip()]
                curr_dir2 = lines2[-1]
                # Redirect wins!
                self.assertEqual(norm_key(curr_dir2), norm_key(redirect_target))
            finally:
                with open(act_bat, "w", encoding="utf-8") as f:
                    f.write(orig_bat)

            # 2. Real PowerShell test
            ps_term = Terminal("powershell", "Windows PowerShell", "powershell.exe")
            argv_ps, cwd_ps, _ = terminal_command(env, ps_term, places)
            self.assertIsInstance(argv_ps, list)

            # Exact list from terminal_command: drop -NoExit, append ; python -c ... to -Command
            ps_run = [arg for arg in argv_ps if arg != "-NoExit"]
            cmd_idx = ps_run.index("-Command")
            ps_run[cmd_idx + 1] = f"{ps_run[cmd_idx + 1]}; {test_py}"

            res_ps = subprocess.run(
                ps_run,
                cwd=cwd_ps,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=proc_env,
                check=True,
            )
            ps_lines = [line.strip() for line in res_ps.stdout.strip().splitlines() if line.strip()]
            self.assertGreaterEqual(len(ps_lines), 2)
            ps_prefix = ps_lines[-2]
            ps_curr_dir = ps_lines[-1]

            self.assertEqual(norm_key(ps_prefix), norm_key(venv_path))
            self.assertEqual(norm_key(ps_curr_dir), norm_key(venv_path))

            # Add redirect: Set-Location '<target_dir>' at the end of Activate.ps1
            act_ps1 = os.path.join(venv_path, "Scripts", "Activate.ps1")
            with open(act_ps1, "r", encoding="utf-8", errors="replace") as f:
                orig_ps1 = f.read()

            sig = "# SIG # Begin signature block"
            line = f"Set-Location '{redirect_target}'\n"
            new_ps1 = orig_ps1.replace(sig, line + sig, 1) if sig in orig_ps1 else orig_ps1.rstrip() + "\n" + line

            try:
                os.makedirs(redirect_target, exist_ok=True)
                with open(act_ps1, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_ps1)

                res_ps2 = subprocess.run(
                    ps_run,
                    cwd=cwd_ps,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=proc_env,
                    check=True,
                )
                ps_lines2 = [line.strip() for line in res_ps2.stdout.strip().splitlines() if line.strip()]
                self.assertGreaterEqual(
                    len(ps_lines2),
                    2,
                    f"PowerShell output too short: {res_ps2.stdout}\nstderr: {res_ps2.stderr}",
                )
                ps_curr_dir2 = ps_lines2[-1]
                # Redirect wins!
                self.assertEqual(
                    norm_key(ps_curr_dir2),
                    norm_key(redirect_target),
                    f"PowerShell redirect mismatch. stdout:\n{res_ps2.stdout}\nstderr:\n{res_ps2.stderr}",
                )
            finally:
                with open(act_ps1, "w", encoding="utf-8", newline="\n") as f:
                    f.write(orig_ps1)

    def test_open_terminal_launch_flags(self) -> None:
        """open_terminal passes CREATE_NEW_CONSOLE for non-wt, and no flags for wt."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            env = Env(tmp, Kind.VENV, "test_env")

            cmd_term = Terminal("cmd", "Command Prompt", "cmd.exe")
            wt_term = Terminal("wt-cmd", "Windows Terminal (Command Prompt)", "wt.exe")

            with patch("subprocess.Popen") as mock_popen:
                open_terminal(env, cmd_term, places)
                self.assertTrue(mock_popen.called)
                args, kwargs = mock_popen.call_args
                self.assertIsInstance(args[0], str)
                self.assertEqual(kwargs["creationflags"], 0x00000010)

            with patch("subprocess.Popen") as mock_popen_wt:
                open_terminal(env, wt_term, places)
                self.assertTrue(mock_popen_wt.called)
                args_wt, kwargs_wt = mock_popen_wt.call_args
                self.assertIsInstance(args_wt[0], str)
                self.assertEqual(kwargs_wt["creationflags"], 0)


if __name__ == "__main__":
    unittest.main()
