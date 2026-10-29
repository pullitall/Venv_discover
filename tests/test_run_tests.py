"""Tests for the hidden desktop test runner (scripts/run_tests.py)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import scripts.run_tests as rt


class TestRunTests(unittest.TestCase):
    def setUp(self) -> None:
        test_tmp = rt.default_test_tmp()
        try:
            os.makedirs(test_tmp, exist_ok=True)
        except Exception:
            pass
        self.temp_dir = tempfile.mkdtemp(prefix="test_rt_", dir=test_tmp)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _create_sample_test(self, filename: str, content: str) -> str:
        filepath = os.path.join(self.temp_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def test_hidden_desktop_passes_and_window_guard(self) -> None:
        """Sample Tk window test passes with 0 windows on owner desktop and >= 1 on hidden desktop."""
        content = (
            "import unittest\n"
            "import tkinter as tk\n"
            "import time\n\n"
            "class SampleTkTest(unittest.TestCase):\n"
            "    def test_tk_window(self):\n"
            "        root = tk.Tk()\n"
            "        root.title('SampleHiddenTkWindow')\n"
            "        root.geometry('200x200+100+100')\n"
            "        root.deiconify()\n"
            "        root.update()\n"
            "        time.sleep(0.3)\n"
            "        root.destroy()\n"
        )
        test_file = self._create_sample_test("test_tk_sample.py", content)

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(rt.last_owner_seen), 0)
        self.assertGreaterEqual(len(rt.last_hidden_seen), 1)
        output = out.getvalue()
        self.assertIn("windows of the test process on the OWNER's desktop: 0", output)

    def test_failing_sample_fails(self) -> None:
        """A failing sample test makes the runner exit non-zero."""
        content = (
            "import unittest\n\n"
            "class SampleFailTest(unittest.TestCase):\n"
            "    def test_fail(self):\n"
            "        self.assertEqual(1, 2, 'Intentional test failure')\n"
        )
        test_file = self._create_sample_test("test_fail_sample.py", content)

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertNotEqual(exit_code, 0)
        output = out.getvalue()
        self.assertIn("FAILED", output)

    def test_timeout_kills_process(self) -> None:
        """A hanging test exceeding timeout is killed with a clear message and process is terminated."""
        content = (
            "import unittest\n"
            "import time\n\n"
            "class SampleHangTest(unittest.TestCase):\n"
            "    def test_hang(self):\n"
            "        time.sleep(10.0)\n"
        )
        test_file = self._create_sample_test("test_hang_sample.py", content)

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            exit_code = rt.main(
                ["--python", sys.executable, "--workers", "1", "--timeout", "2.0", test_file]
            )

        self.assertNotEqual(exit_code, 0)
        output = out.getvalue()
        self.assertIn("TIMED OUT after 2.0s", output)
        self.assertTrue(rt.last_workers[0].timed_out)

        # Verify child process is dead
        child_pid = rt.last_workers[0].pid
        self.assertGreater(child_pid, 0)
        time.sleep(0.1)

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hProcess = rt.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | rt.SYNCHRONIZE, False, child_pid
        )
        if hProcess:
            code = wt.DWORD()
            rt.kernel32.GetExitCodeProcess(hProcess, ctypes.byref(code))
            rt.kernel32.CloseHandle(hProcess)
            STILL_ACTIVE = 259
            self.assertNotEqual(code.value, STILL_ACTIVE)

    def test_desktop_closed_afterwards(self) -> None:
        """The hidden desktop is closed after run: opening it again with OpenDesktopW fails."""
        content = (
            "import unittest\n\n"
            "class SampleQuickTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        test_file = self._create_sample_test("test_quick_sample.py", content)

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 0)
        desk_name = rt.last_created_desktop_name
        self.assertIsNotNone(desk_name)

        # Attempt to open desktop by name after runner finished
        hdesk = rt.user32.OpenDesktopW(desk_name, 0, False, rt.GENERIC_ALL)
        self.assertFalse(bool(hdesk))

    def test_owner_window_guard_fails_run(self) -> None:
        """If a window appears on the owner's desktop, main() fails with exit code 1 and prints FAIL."""
        content = (
            "import unittest\n\n"
            "class SampleQuickTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        test_file = self._create_sample_test("test_quick_sample.py", content)

        out = io.StringIO()
        with mock.patch("scripts.run_tests.windows_on_owner_desktop", return_value=[(99999, "Leaked Window")]):
            with redirect_stdout(out), redirect_stderr(out):
                exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 1)
        output = out.getvalue()
        self.assertIn("FAIL: 1 window(s) appeared on the owner's desktop", output)

    def test_job_kills_grandchild_on_close(self) -> None:
        """Processes spawned by tests (grandchildren) are assigned to the Job Object and killed on close."""
        pid_file = os.path.join(self.temp_dir, "grandchild.pid")
        escaped_pid_file = pid_file.replace("\\", "\\\\")
        content = (
            "import unittest\n"
            "import subprocess\n"
            "import sys\n\n"
            "class GrandchildTest(unittest.TestCase):\n"
            "    def test_spawn(self):\n"
            f"        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"        with open(r'{escaped_pid_file}', 'w') as f:\n"
            "            f.write(str(p.pid))\n"
        )
        test_file = self._create_sample_test("test_grandchild.py", content)

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.isfile(pid_file))
        with open(pid_file, "r") as f:
            grandchild_pid = int(f.read().strip())

        self.assertGreater(grandchild_pid, 0)
        time.sleep(0.1)

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hProcess = rt.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | rt.SYNCHRONIZE, False, grandchild_pid
        )
        if hProcess:
            code = wt.DWORD()
            rt.kernel32.GetExitCodeProcess(hProcess, ctypes.byref(code))
            rt.kernel32.CloseHandle(hProcess)
            STILL_ACTIVE = 259
            self.assertNotEqual(code.value, STILL_ACTIVE)

    def test_temp_env_isolation(self) -> None:
        """TEMP and TMP for child processes are set to run_tests.default_test_tmp()."""
        content = (
            "import unittest\n"
            "import os\n\n"
            "class TempEnvTest(unittest.TestCase):\n"
            "    def test_temp(self):\n"
            f"        expected = {rt.default_test_tmp().lower()!r}\n"
            "        self.assertEqual(os.environ.get('TEMP', '').lower(), expected)\n"
            "        self.assertEqual(os.environ.get('TMP', '').lower(), expected)\n"
        )
        test_file = self._create_sample_test("test_temp_env.py", content)

        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TEMP": self.temp_dir, "TMP": self.temp_dir}):
            with redirect_stdout(out), redirect_stderr(out):
                exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 0)

    def test_clipboard_guard_fails_when_test_changes_clipboard(self) -> None:
        """When a test/job process changes the clipboard, the runner fails with non-zero exit."""
        content = (
            "import unittest\n\n"
            "class SampleQuickTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        test_file = self._create_sample_test("test_quick_sample.py", content)

        seq_state = [100]

        def mock_seq() -> int:
            val = seq_state[0]
            seq_state[0] = 101
            return val

        out = io.StringIO()
        with mock.patch("scripts.run_tests.get_clipboard_sequence_number", side_effect=mock_seq), \
             mock.patch("scripts.run_tests.get_clipboard_owner", return_value=0):  # NULL owner = attributed to test
            with redirect_stdout(out), redirect_stderr(out):
                exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertNotEqual(exit_code, 0)
        output = out.getvalue()
        self.assertIn("FAIL: A test changed the owner's clipboard", output)

    def test_clipboard_guard_ignores_external_copy(self) -> None:
        """When another external program (e.g. owner) copies, print note and do NOT fail."""
        content = (
            "import unittest\n\n"
            "class SampleQuickTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        pass\n"
        )
        test_file = self._create_sample_test("test_quick_sample.py", content)

        seq_state = [100]

        def mock_seq() -> int:
            val = seq_state[0]
            seq_state[0] = 101
            return val

        out = io.StringIO()
        with mock.patch("scripts.run_tests.get_clipboard_sequence_number", side_effect=mock_seq), \
             mock.patch("scripts.run_tests.get_clipboard_owner", return_value=8888), \
             mock.patch("scripts.run_tests.get_window_pid", return_value=99999):  # 99999 is external PID
            with redirect_stdout(out), redirect_stderr(out):
                exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 0)
        output = out.getvalue()
        self.assertIn("the clipboard changed by another program", output)
        self.assertNotIn("FAIL: A test changed the owner's clipboard", output)

    def test_window_guard_mocked(self) -> None:
        """Window guard detects visible windows for matching PIDs via EnumWindows and returns 0 for non-matching."""
        target_pid = 9876

        def fake_enum(cb, lparam):
            cb(12345, lparam)
            return True

        def fake_pid(hwnd, ptr):
            ptr._obj.value = target_pid
            return target_pid

        def fake_rect(hwnd, ptr):
            r = ptr._obj
            r.left, r.top, r.right, r.bottom = 10, 10, 200, 200
            return True

        with mock.patch.object(rt.user32, "OpenDesktopW", return_value=None), \
             mock.patch.object(rt.user32, "EnumWindows", side_effect=fake_enum), \
             mock.patch.object(rt.user32, "GetWindowThreadProcessId", side_effect=fake_pid), \
             mock.patch.object(rt.user32, "IsWindowVisible", return_value=True), \
             mock.patch.object(rt.user32, "GetWindowRect", side_effect=fake_rect), \
             mock.patch.object(rt, "get_window_title", return_value="Mocked Fake Window"):

            found = rt.windows_on_owner_desktop({target_pid})
            self.assertEqual(found, [(12345, "Mocked Fake Window")])

            not_found = rt.windows_on_owner_desktop({1111})
            self.assertEqual(not_found, [])

        # Real 0-window check with empty set
        self.assertEqual(rt.windows_on_owner_desktop(set()), [])

    def test_check_clipboard_change_test_pid_blamed(self) -> None:
        """check_clipboard_change records a violation when owner_pid matches an active worker PID."""
        violations: list[tuple[int, int]] = []
        with mock.patch("scripts.run_tests.get_clipboard_sequence_number", return_value=101), \
             mock.patch("scripts.run_tests.get_clipboard_owner", return_value=5), \
             mock.patch("scripts.run_tests.get_window_pid", return_value=4242):
            res = rt.check_clipboard_change(None, {4242}, 100, violations)

        self.assertEqual(res, 101)
        self.assertEqual(violations, [(101, 4242)])

    def test_check_clipboard_change_grandchild_job_pid_blamed(self) -> None:
        """check_clipboard_change records a violation when owner_pid matches a job PID (grandchild)."""
        violations: list[tuple[int, int]] = []
        fake_job = wt.HANDLE(1234)
        with mock.patch("scripts.run_tests.get_clipboard_sequence_number", return_value=101), \
             mock.patch("scripts.run_tests.get_clipboard_owner", return_value=5), \
             mock.patch("scripts.run_tests.get_window_pid", return_value=4242), \
             mock.patch("scripts.run_tests.get_job_process_ids", return_value={4242}):
            res = rt.check_clipboard_change(fake_job, set(), 100, violations)

        self.assertEqual(res, 101)
        self.assertEqual(violations, [(101, 4242)])

    def test_get_job_process_ids_real(self) -> None:
        """get_job_process_ids queries QueryInformationJobObject for all PIDs assigned to the job."""
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            hJob = rt.create_job_object()
            self.assertIsNotNone(hJob)
            try:
                PROCESS_ALL_ACCESS = 0x001F0FFF
                hProcess = rt.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, child.pid)
                self.assertTrue(hProcess)
                try:
                    ok = rt.kernel32.AssignProcessToJobObject(hJob, hProcess)
                    self.assertTrue(ok)
                finally:
                    rt.kernel32.CloseHandle(hProcess)

                pids = rt.get_job_process_ids(hJob)
                self.assertEqual(pids, {child.pid})
            finally:
                rt.kernel32.CloseHandle(hJob)

            # Child process terminates cleanly because of KILL_ON_JOB_CLOSE
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_grandchild_window_on_owner_desktop_fails_run(self) -> None:
        """When a window belongs to a grandchild PID in the job, owner window guard catches it and fails."""
        pid_file = os.path.join(self.temp_dir, "grandchild_win.pid")
        escaped_pid_file = pid_file.replace("\\", "\\\\")
        content = (
            "import unittest\n"
            "import subprocess\n"
            "import sys\n"
            "import time\n\n"
            "class GrandchildWinTest(unittest.TestCase):\n"
            "    def test_spawn(self):\n"
            "        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])\n"
            f"        with open(r'{escaped_pid_file}', 'w') as f:\n"
            "            f.write(str(p.pid))\n"
            "        time.sleep(0.5)\n"
        )
        test_file = self._create_sample_test("test_grandchild_win.py", content)

        def mock_owner_windows(pids: set[int]) -> list[tuple[int, str]]:
            # Return a leaked window if pids contains more than 1 PID (i.e. includes grandchild PID)
            if len(pids) > 1:
                return [(99999, "Leaked Grandchild Window")]
            return []

        out = io.StringIO()
        with mock.patch("scripts.run_tests.windows_on_owner_desktop", side_effect=mock_owner_windows):
            with redirect_stdout(out), redirect_stderr(out):
                exit_code = rt.main(["--python", sys.executable, "--workers", "1", test_file])

        self.assertEqual(exit_code, 1)
        output = out.getvalue()
        self.assertIn("FAIL: 1 window(s) appeared on the owner's desktop", output)


if __name__ == "__main__":
    unittest.main()
