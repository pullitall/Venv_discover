import json
import os
import subprocess
import unittest

from tests import fakes
from venvdiscover.connections import (
    LnkInfo,
    add_env_line,
    find_kernels,
    find_shortcuts,
    kernel_dirs,
    read_lnk,
    remove_env_line,
    shortcut_dirs,
)
from venvdiscover.model import norm_key


def _make_powershell_shortcut(
    lnk_path: str,
    target: str,
    arguments: str = "",
    working_dir: str = "",
    description: str = "",
) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(lnk_path)), exist_ok=True)
    target_esc = target.replace("'", "''")
    args_esc = arguments.replace("'", "''")
    workdir_esc = working_dir.replace("'", "''")
    desc_esc = description.replace("'", "''")
    lnk_esc = os.path.abspath(lnk_path).replace("'", "''")

    desc_line = f"$s.Description = '{desc_esc}'; " if description else ""
    ps = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk_esc}'); "
        f"$s.TargetPath = '{target_esc}'; "
        f"$s.Arguments = '{args_esc}'; "
        f"$s.WorkingDirectory = '{workdir_esc}'; "
        f"{desc_line}"
        f"$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


class ConnectionsTests(unittest.TestCase):
    def test_env_lines(self):
        with fakes.tmpdir() as tmp:
            env1 = os.path.join(tmp, "envs", "env1")
            env2 = os.path.join(tmp, "envs", "env2")
            env3 = os.path.join(tmp, "envs", "env3")
            env_file = os.path.join(tmp, "environments.txt")

            # A file with 3 lines, one of them in different case and with a trailing backslash
            content = f"{env1}\n{env2.upper()}\\\n{env3}\n"
            fakes.write_file(env_file, content)

            # remove_env_line removes the matching one and returns True
            removed = remove_env_line(env_file, env2)
            self.assertTrue(removed)

            with open(env_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(norm_key(lines[0]), norm_key(env1))
            self.assertEqual(norm_key(lines[1]), norm_key(env3))

            # a second call returns False
            self.assertFalse(remove_env_line(env_file, env2))

            # add_env_line puts it back once and only once
            add_env_line(env_file, env2)
            with open(env_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(norm_key(lines[2]), norm_key(env2))

            add_env_line(env_file, env2)
            with open(env_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 3)

    def test_env_lines_crlf_and_polish(self):
        with fakes.tmpdir() as tmp:
            env_file = os.path.join(tmp, "environments.txt")
            polish_path = r"D:\Projekty\Pięściarz\venv"
            to_remove = r"C:\miniconda3\envs\torch26"
            other_path = r"C:\Users\Alex\env"

            # 3 CRLF lines
            original_bytes = (
                f"{polish_path}\r\n{to_remove}\r\n{other_path}\r\n".encode("utf-8")
            )
            with open(env_file, "wb") as f:
                f.write(original_bytes)

            removed = remove_env_line(env_file, to_remove)
            self.assertTrue(removed)

            with open(env_file, "rb") as f:
                resulting_bytes = f.read()

            expected_bytes = f"{polish_path}\r\n{other_path}\r\n".encode("utf-8")
            self.assertEqual(resulting_bytes, expected_bytes)

    def test_env_lines_cp1250(self):
        with fakes.tmpdir() as tmp:
            env_file = os.path.join(tmp, "environments.txt")
            polish_path = r"D:\Zażółć\Gęślą\Jaźń\venv"
            to_remove = r"C:\miniconda3\envs\torch26"

            # encoded as cp1250 (Windows Polish ANSI codepage)
            original_bytes = (
                f"{polish_path}\r\n{to_remove}\r\n".encode("cp1250")
            )
            with open(env_file, "wb") as f:
                f.write(original_bytes)

            removed = remove_env_line(env_file, to_remove)
            self.assertTrue(removed)

            with open(env_file, "rb") as f:
                resulting_bytes = f.read()

            expected_bytes = f"{polish_path}\r\n".encode("cp1250")
            self.assertEqual(resulting_bytes, expected_bytes)

    def test_find_kernels(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            dirs = kernel_dirs(places)

            env1 = fakes.make_venv(tmp, "env1")
            env2 = fakes.make_venv(tmp, "env2")

            # kernel matching env1
            k1_dir = os.path.join(dirs[0], "kernel1")
            os.makedirs(k1_dir, exist_ok=True)
            k1_json = os.path.join(k1_dir, "kernel.json")
            fakes.write_file(
                k1_json,
                json.dumps({
                    "argv": [os.path.join(env1, "Scripts", "python.exe"), "-m", "ipykernel_launcher"],
                    "display_name": "Kernel 1",
                }),
            )

            # kernel matching env2
            k2_dir = os.path.join(dirs[0], "kernel2")
            os.makedirs(k2_dir, exist_ok=True)
            k2_json = os.path.join(k2_dir, "kernel.json")
            fakes.write_file(
                k2_json,
                json.dumps({
                    "argv": [os.path.join(env2, "python.exe"), "-m", "ipykernel_launcher"],
                    "display_name": "Kernel 2",
                }),
            )

            # broken kernel.json
            k_broken = os.path.join(dirs[1], "broken")
            os.makedirs(k_broken, exist_ok=True)
            fakes.write_file(os.path.join(k_broken, "kernel.json"), "invalid json {[[")

            found1 = find_kernels(env1, dirs)
            self.assertEqual(len(found1), 1)
            self.assertEqual(norm_key(found1[0]), norm_key(k1_dir))

            found2 = find_kernels(env2, dirs)
            self.assertEqual(len(found2), 1)
            self.assertEqual(norm_key(found2[0]), norm_key(k2_dir))

    def test_read_lnk_real(self):
        with fakes.tmpdir() as tmp:
            env = fakes.make_venv(tmp, "myenv")
            python_exe = os.path.join(env, "Scripts", "python.exe")
            lnk_path = os.path.join(tmp, "idle.lnk")

            _make_powershell_shortcut(
                lnk_path=lnk_path,
                target=python_exe,
                arguments="-m idlelib",
                working_dir=env,
                description="Idle shortcut description for NAME field testing",
            )

            info = read_lnk(lnk_path)
            self.assertIsNotNone(info)
            self.assertEqual(norm_key(info.target), norm_key(python_exe))
            self.assertEqual(info.arguments, "-m idlelib")
            self.assertEqual(norm_key(info.working_dir), norm_key(env))

    def test_find_shortcuts(self):
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            dirs = shortcut_dirs(places)

            env = fakes.make_venv(tmp, "myenv")
            sibling_env = fakes.make_venv(tmp, "myenv2")

            # Shortcut 1: targets cmd.exe with arguments /K "<env>\Scripts\activate.bat"
            s1_path = os.path.join(dirs[0], "Env Prompt.lnk")
            _make_powershell_shortcut(
                lnk_path=s1_path,
                target=os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
                arguments=f'/K "{os.path.join(env, "Scripts", "activate.bat")}"',
                working_dir=env,
                description="Env prompt with description",
            )

            # Shortcut 2: sibling env named <env>2
            s2_path = os.path.join(dirs[0], "Sibling Prompt.lnk")
            _make_powershell_shortcut(
                lnk_path=s2_path,
                target=os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
                arguments=f'/K "{os.path.join(sibling_env, "Scripts", "activate.bat")}"',
                working_dir=sibling_env,
            )

            found = find_shortcuts(env, dirs)
            self.assertEqual(len(found), 1)
            self.assertEqual(norm_key(found[0]), norm_key(s1_path))

    def test_read_lnk_garbage(self):
        with fakes.tmpdir() as tmp:
            empty_file = os.path.join(tmp, "empty.lnk")
            fakes.write_file(empty_file, b"")
            self.assertIsNone(read_lnk(empty_file))

            garbage_file = os.path.join(tmp, "garbage.lnk")
            fakes.write_file(garbage_file, b"1234567890abcdef" * 10)
            self.assertIsNone(read_lnk(garbage_file))

            bad_header = os.path.join(tmp, "bad_header.lnk")
            fakes.write_file(bad_header, b"\x00" * 100)
            self.assertIsNone(read_lnk(bad_header))

            # valid header size 0x4C but bad CLSID
            bad_clsid = bytearray(b"\x00" * 100)
            bad_clsid[0:4] = b"\x4C\x00\x00\x00"
            bad_clsid_file = os.path.join(tmp, "bad_clsid.lnk")
            fakes.write_file(bad_clsid_file, bytes(bad_clsid))
            self.assertIsNone(read_lnk(bad_clsid_file))


if __name__ == "__main__":
    unittest.main()
