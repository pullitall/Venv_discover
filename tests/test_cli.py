"""Unit tests for venvdiscover-cli (venvdiscover/cli.py)."""

from __future__ import annotations

import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests import fakes
from venvdiscover.cli import main
from venvdiscover.model import Package
from venvdiscover.scanner import Progress, ScanDone


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = fakes.tmpdir()
        self.tmp = self.tmp_ctx.name
        self.places = fakes.fake_places(self.tmp)
        self.environ = {
            "LOCALAPPDATA": os.path.join(self.tmp, "local"),
            "APPDATA": os.path.join(self.tmp, "roaming"),
            "USERPROFILE": os.path.join(self.tmp, "home"),
            "PROGRAMDATA": os.path.join(self.tmp, "pdata"),
            "TEMP": os.path.join(self.tmp, "temp"),
            "TMP": os.path.join(self.tmp, "temp"),
        }
        for d in self.environ.values():
            os.makedirs(d, exist_ok=True)

        # Isolated environment for subprocess tests preventing host profile contamination
        self.fake_env = dict(os.environ)
        self.fake_env.update(self.environ)

        # Record state of host %LOCALAPPDATA%\VenvDiscover to verify it is never touched
        real_local = os.environ.get("LOCALAPPDATA", "")
        self.host_vd_dir = os.path.join(real_local, "VenvDiscover") if real_local else None
        if self.host_vd_dir and os.path.exists(self.host_vd_dir):
            self.host_vd_mtime = os.path.getmtime(self.host_vd_dir)
            self.host_vd_files = set(os.listdir(self.host_vd_dir))
        else:
            self.host_vd_mtime = None
            self.host_vd_files = None

    def tearDown(self) -> None:
        # Assert host %LOCALAPPDATA%\VenvDiscover was never touched
        if self.host_vd_dir:
            if self.host_vd_mtime is None:
                self.assertFalse(
                    os.path.exists(self.host_vd_dir),
                    "Host %LOCALAPPDATA%\\VenvDiscover was created during tests!",
                )
            else:
                self.assertEqual(
                    os.path.getmtime(self.host_vd_dir),
                    self.host_vd_mtime,
                    "Host %LOCALAPPDATA%\\VenvDiscover mtime was modified during tests!",
                )
                self.assertEqual(
                    set(os.listdir(self.host_vd_dir)),
                    self.host_vd_files,
                    "Host %LOCALAPPDATA%\\VenvDiscover files were modified during tests!",
                )
        self.tmp_ctx.cleanup()

    def test_json_schema_and_types(self) -> None:
        vdir = fakes.make_venv(
            self.tmp,
            "my_venv",
            version="3.11.4",
            packages={"numpy": "1.26.4", "requests": "2.31.0"},
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        code = main(
            ["--json", "--packages", "--sizes", "--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(code, 0)
        # Verify stdout parses cleanly as JSON (no progress text leaked into stdout)
        raw_json = stdout.getvalue()
        data = json.loads(raw_json)

        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["app"], "1.1")
        datetime.datetime.fromisoformat(data["scanned_at"])

        envs = data["envs"]
        self.assertEqual(len(envs), 1)
        e = envs[0]

        self.assertIn("path", e)
        self.assertIn("name", e)
        self.assertIn("kind", e)
        self.assertIn("locked", e)
        self.assertIn("python", e)
        self.assertIn("size", e)
        self.assertIn("last_used", e)
        self.assertIn("conda_root", e)
        self.assertIn("packages", e)
        self.assertIn("connections", e)

        self.assertFalse(e["path"].startswith("\\\\?\\"))
        self.assertEqual(e["kind"], "venv")
        self.assertFalse(e["locked"])
        self.assertEqual(e["python"], "3.11.4")
        self.assertIsInstance(e["size"], int)
        self.assertGreater(e["size"], 0)
        # size_unique: what deleting frees (nothing is hard-linked from a package cache here)
        self.assertEqual(e["size_unique"], e["size"])
        self.assertIsNone(e["conda_root"])

        pkgs = e["packages"]
        self.assertEqual(len(pkgs), 2)
        self.assertEqual(pkgs[0], {"name": "numpy", "version": "1.26.4", "source": "pip"})
        self.assertEqual(pkgs[1], {"name": "requests", "version": "2.31.0", "source": "pip"})

        conns = e["connections"]
        self.assertIsInstance(conns["environments_txt"], bool)
        self.assertIsInstance(conns["kernels"], list)
        self.assertIsInstance(conns["shortcuts"], list)

    def test_json_packages_and_sizes_null_when_omitted(self) -> None:
        vdir = fakes.make_venv(
            self.tmp,
            "fast_env",
            version="3.12.1",
            packages={"click": "8.1.7"},
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        code = main(
            ["--json", "--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(len(data["envs"]), 1)
        e = data["envs"][0]
        self.assertIsNone(e["packages"])
        self.assertIsNone(e["size"])
        self.assertIsNone(e["size_unique"])
        self.assertIsNone(e["last_used"])

    def test_search_name_and_version(self) -> None:
        env1 = fakes.make_venv(
            self.tmp, "env1", version="3.11",
            packages={"numpy": "1.26.4", "scipy": "1.11.0"},
        )
        env2 = fakes.make_venv(
            self.tmp, "env2", version="3.11",
            packages={"numpy": "2.1.0"},
        )
        env3 = fakes.make_venv(
            self.tmp, "env3", version="3.11",
            packages={"flask": "3.0.0"},
        )

        # 1. Search numpy==1.26.4
        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", env1, env2, env3, "--search", "numpy==1.26.4"],
            places=self.places, environ=self.environ, stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual([e["name"] for e in data["envs"]], ["env1"])

        # 2. Search numpy (matches any version and PEP 503 normalization)
        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", env1, env2, env3, "--search", "NumPy"],
            places=self.places, environ=self.environ, stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(sorted(e["name"] for e in data["envs"]), ["env1", "env2"])

        # 3. Search non-existent
        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", env1, env2, env3, "--search", "django"],
            places=self.places, environ=self.environ, stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["envs"], [])

    def test_known_only_skips_walk(self) -> None:
        known_env = fakes.make_conda_env(self.tmp, "known_conda")
        os.makedirs(os.path.dirname(self.places.environments_txt), exist_ok=True)
        with open(self.places.environments_txt, "w", encoding="utf-8") as f:
            f.write(known_env + "\n")

        walk_dir = os.path.join(self.tmp, "deep", "nested", "projects")
        walk_env = fakes.make_venv(walk_dir, "walk_env")

        stdout = io.StringIO()
        code = main(
            ["--json", "--known-only"],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        paths = [e["path"] for e in data["envs"]]

        self.assertIn(known_env, paths)
        self.assertNotIn(walk_env, paths)

    def test_paths_limits_walk(self) -> None:
        dir_a = os.path.join(self.tmp, "group_a")
        dir_b = os.path.join(self.tmp, "group_b")
        env_a = fakes.make_venv(dir_a, "env_a")
        env_b = fakes.make_venv(dir_b, "env_b")

        # Also add a known conda env in environments.txt outside dir_a (kills Mutation N5)
        known_env = fakes.make_conda_env(self.tmp, "known_conda")
        os.makedirs(os.path.dirname(self.places.environments_txt), exist_ok=True)
        with open(self.places.environments_txt, "w", encoding="utf-8") as f:
            f.write(known_env + "\n")

        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", dir_a],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        paths = [e["path"] for e in data["envs"]]

        self.assertIn(env_a, paths)
        self.assertNotIn(env_b, paths)
        self.assertNotIn(known_env, paths)

    def test_human_readable_table_format(self) -> None:
        vdir = fakes.make_venv(self.tmp, "demo_env", version="3.11.2", packages={"a": "1"})
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = main(
            ["--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 0)
        out = stdout.getvalue()

        self.assertIn("Name", out)
        self.assertIn("Kind", out)
        self.assertIn("Python", out)
        self.assertIn("Packages", out)
        self.assertIn("Size", out)
        self.assertIn("Last used", out)
        self.assertIn("Location", out)
        self.assertIn("demo_env", out)
        self.assertIn("Found 1 environment.", out)

    def test_table_mode_unencodable_path(self) -> None:
        """Table mode reconfigures streams with errors='replace', handling characters unencodable in stdout's encoding."""
        vdir = fakes.make_venv(self.tmp, "env_環境", version="3.11.2")
        raw_buffer = io.BytesIO()
        wrapper = io.TextIOWrapper(raw_buffer, encoding="ascii", errors="strict")

        code = main(
            ["--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=wrapper,
        )
        self.assertEqual(code, 0)
        wrapper.flush()
        out = raw_buffer.getvalue().decode("ascii", errors="replace")
        self.assertIn("env_??", out)
        self.assertIn("Found 1 environment.", out)

    def test_stdout_write_error_returns_code_3(self) -> None:
        """If writing results to stdout raises an IO/OS error, return exit code 3."""
        vdir = fakes.make_venv(self.tmp, "test_env")

        class FailingStream(io.StringIO):
            def write(self, s: str) -> int:
                raise OSError("simulated disk full")

        broken_stdout = FailingStream()
        stderr = io.StringIO()
        code = main(
            ["--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=broken_stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 3)
        self.assertIn("output error: simulated disk full", stderr.getvalue())

    def test_progress_indicator_formatting(self) -> None:
        """TTY stderr uses carriage return and erase line; non-TTY writes clean plain lines."""
        class FakeProgressScanner:
            def __init__(self, out, **kwargs):
                self.out = out

            def start(self):
                self.out.put(Progress("Searching folder..."))
                self.out.put(ScanDone(seconds=0.1, total=0))

            def stop(self):
                pass

        # 1. Non-TTY stderr: lines end with newline, no control characters
        stderr_non_tty = io.StringIO()
        stderr_non_tty.isatty = lambda: False  # type: ignore[assignment]
        code = main(
            ["--known-only"],
            places=self.places,
            environ=self.environ,
            stdout=io.StringIO(),
            stderr=stderr_non_tty,
            scanner_factory=FakeProgressScanner,
        )
        self.assertEqual(code, 0)
        out_non_tty = stderr_non_tty.getvalue()
        self.assertIn("Searching folder...\n", out_non_tty)
        self.assertNotIn("\r", out_non_tty)
        self.assertNotIn("\x1b[K", out_non_tty)

        # 2. TTY stderr: uses carriage return and ANSI erase line
        stderr_tty = io.StringIO()
        stderr_tty.isatty = lambda: True  # type: ignore[assignment]
        code = main(
            ["--known-only"],
            places=self.places,
            environ=self.environ,
            stdout=io.StringIO(),
            stderr=stderr_tty,
            scanner_factory=FakeProgressScanner,
        )
        self.assertEqual(code, 0)
        out_tty = stderr_tty.getvalue()
        self.assertIn("\rSearching folder...\x1b[K", out_tty)
        self.assertIn("\r\x1b[K", out_tty)

    def test_bad_arguments_exit_code_2(self) -> None:
        stderr = io.StringIO()
        code = main(["--known-only", "--paths", self.tmp], stderr=stderr)
        self.assertEqual(code, 2)
        self.assertIn("cannot be used together", stderr.getvalue())

        stderr = io.StringIO()
        bad_dir = os.path.join(self.tmp, "does_not_exist_xyz")
        code = main(["--paths", bad_dir], stderr=stderr)
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr.getvalue())

        stderr = io.StringIO()
        code = main(["--search", "   "], stderr=stderr)
        self.assertEqual(code, 2)
        self.assertIn("--search requires a non-empty package query", stderr.getvalue())

        stderr = io.StringIO()
        code = main(["--search", "numpy=="], stderr=stderr)
        self.assertEqual(code, 2)
        self.assertIn("requires both name and version", stderr.getvalue())

        stderr = io.StringIO()
        code = main(["--no-such-option"], stderr=stderr)
        self.assertEqual(code, 2)

    def test_bad_arguments_with_no_stderr_never_prints_to_stdout(self) -> None:
        """When sys.stderr is None, argument parsing errors must never leak usage to sys.stdout."""
        fake_stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", fake_stdout), mock.patch.object(sys, "stderr", None):
            code = main(["--bogus-option-xyz"])
        self.assertEqual(code, 2)
        self.assertEqual(fake_stdout.getvalue(), "")

    def test_cli_never_writes_outside_cache_and_never_deletes(self) -> None:
        vdir = fakes.make_venv(self.tmp, "test_env")
        pre_files = set()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                pre_files.add(os.path.join(root, f))

        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", vdir],
            places=self.places,
            environ=self.environ,
            stdout=stdout,
        )
        self.assertEqual(code, 0)

        post_files = set()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                post_files.add(os.path.join(root, f))

        self.assertTrue(pre_files.issubset(post_files))

        new_files = post_files - pre_files
        for nf in new_files:
            rel = os.path.relpath(nf, self.places.app_dir)
            self.assertEqual(rel, "cache.json")

    def test_caches_flag(self) -> None:
        vdir = fakes.make_venv(self.tmp, "cache_env")

        # Create a fake pip cache under LOCALAPPDATA
        pip_cache = os.path.join(self.places.localappdata, "pip", "Cache")
        wheels_dir = os.path.join(pip_cache, "wheels")
        os.makedirs(wheels_dir, exist_ok=True)
        fakes.write_file(os.path.join(wheels_dir, "pkg.whl"), "dummy wheel content")

        # Without --caches: "caches" key must be omitted
        stdout = io.StringIO()
        code = main(["--json", "--paths", vdir], places=self.places, environ=self.environ, stdout=stdout)
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertNotIn("caches", data)

        # With --caches: "caches" key lists it (tool, path, size)
        stdout = io.StringIO()
        code = main(["--json", "--paths", vdir, "--caches"], places=self.places, environ=self.environ, stdout=stdout)
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertIn("caches", data)
        self.assertIsInstance(data["caches"], list)
        pip_entry = next((c for c in data["caches"] if c.get("tool") == "pip"), None)
        self.assertIsNotNone(pip_entry)
        self.assertIn("path", pip_entry)
        self.assertIn("size", pip_entry)
        self.assertGreater(pip_entry["size"], 0)

    def test_pep503_normalization_search(self) -> None:
        # Package name with underscores vs hyphens
        vdir = fakes.make_venv(
            self.tmp, "env_pep",
            packages={"my_custom_package": "1.0.0"},
        )
        stdout = io.StringIO()
        code = main(
            ["--json", "--paths", vdir, "--search", "my-custom-package==1.0.0"],
            places=self.places, environ=self.environ, stdout=stdout,
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(len(data["envs"]), 1)
        self.assertEqual(data["envs"][0]["name"], "env_pep")

    def test_scan_error_exit_code_3(self) -> None:
        stderr = io.StringIO()

        def broken_scanner(**kwargs):
            raise RuntimeError("simulated scanner explosion")

        code = main(
            ["--paths", self.tmp],
            places=self.places,
            environ=self.environ,
            stderr=stderr,
            scanner_factory=broken_scanner,
        )
        self.assertEqual(code, 3)
        self.assertIn("scan error: simulated scanner explosion", stderr.getvalue())

    def test_stdout_none_exits_code_4_quietly(self) -> None:
        """When sys.stdout is None (e.g. bare windowed exe run), exit 4 quietly without raising."""
        # 1. Normal run with stdout=None
        code = main(["--json", "--known-only"], stdout=None, stderr=None)
        self.assertEqual(code, 4)

        # 2. Bad arguments with stdout=None
        code = main(["--invalid-arg-xyz"], stdout=None, stderr=None)
        self.assertEqual(code, 4)

        # 3. Help flag with stdout=None
        code = main(["--help"], stdout=None, stderr=None)
        self.assertEqual(code, 4)

    def test_unexpected_exception_returns_code_3_no_traceback(self) -> None:
        """Any unexpected exception inside the scan returns 3 with no unhandled traceback."""
        class ExplodingScanner:
            def __init__(self, **kwargs):
                pass

            def start(self):
                raise KeyError("unexpected internal failure")

        stderr = io.StringIO()
        code = main(
            ["--known-only"],
            places=self.places,
            environ=self.environ,
            stdout=io.StringIO(),
            stderr=stderr,
            scanner_factory=ExplodingScanner,
        )
        self.assertEqual(code, 3)
        self.assertIn("error: 'unexpected internal failure'", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_catch_all_outside_scanner_returns_code_3(self) -> None:
        """Any unexpected exception outside the scanner (e.g. store failure) returns 3 with no traceback."""
        stderr = io.StringIO()
        with mock.patch("venvdiscover.store.load_cache", side_effect=RuntimeError("cache disk failure")):
            code = main(
                ["--known-only"],
                places=self.places,
                environ=self.environ,
                stdout=io.StringIO(),
                stderr=stderr,
            )
        self.assertEqual(code, 3)
        self.assertIn("error: cache disk failure", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_path_does_not_import_tkinter(self) -> None:
        """The CLI module and VenvDiscover.pyw CLI dispatch must never import tkinter."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # 1. Import venvdiscover.cli
        check_code = (
            "import sys\n"
            "import venvdiscover.cli\n"
            "assert 'tkinter' not in sys.modules, 'venvdiscover.cli imported tkinter'\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", check_code],
            cwd=repo_root,
            env=self.fake_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

        # 2. Run real VenvDiscover.pyw via runpy with CLI argument
        check_pyw = (
            "import runpy, sys\n"
            "sys.argv = ['VenvDiscover.pyw', '--help']\n"
            "try:\n"
            "    runpy.run_path('VenvDiscover.pyw', run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    assert exc.code == 0, f'unexpected exit code: {exc.code}'\n"
            "assert 'tkinter' not in sys.modules, 'VenvDiscover.pyw imported tkinter in CLI mode'\n"
        )
        res_pyw = subprocess.run(
            [sys.executable, "-c", check_pyw],
            cwd=repo_root,
            env=self.fake_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_pyw.returncode, 0, res_pyw.stderr)

    def test_dispatch_logic_gui_vs_cli(self) -> None:
        """Test dispatch in VenvDiscover.pyw via runpy.run_path: args -> CLI, no args -> GUI."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dispatch_test = (
            "import runpy, sys, unittest.mock as mock\n"
            "import venvdiscover.cli\n"
            "\n"
            "# Case A: with arguments -> calls CLI, tkinter not imported\n"
            "cli_called = False\n"
            "def fake_cli(argv):\n"
            "    global cli_called\n"
            "    cli_called = True\n"
            "    assert 'tkinter' not in sys.modules, 'tkinter was imported in CLI mode'\n"
            "    return 42\n"
            "\n"
            "sys.argv = ['VenvDiscover.pyw', '--json']\n"
            "with mock.patch.object(venvdiscover.cli, 'main', side_effect=fake_cli):\n"
            "    try:\n"
            "        runpy.run_path('VenvDiscover.pyw', run_name='__main__')\n"
            "    except SystemExit as exc:\n"
            "        assert exc.code == 42\n"
            "assert cli_called, 'cli.main was not called'\n"
            "assert 'tkinter' not in sys.modules, 'tkinter leaked into sys.modules'\n"
            "\n"
            "# Case B: without arguments -> calls GUI\n"
            "import venvdiscover.ui.app\n"
            "gui_called = False\n"
            "def fake_gui():\n"
            "    global gui_called\n"
            "    gui_called = True\n"
            "def broken_cli(argv):\n"
            "    raise AssertionError('CLI called with no args')\n"
            "\n"
            "sys.argv = ['VenvDiscover.pyw']\n"
            "with mock.patch.object(venvdiscover.cli, 'main', side_effect=broken_cli):\n"
            "    with mock.patch.object(venvdiscover.ui.app, 'main', side_effect=fake_gui):\n"
            "        try:\n"
            "            runpy.run_path('VenvDiscover.pyw', run_name='__main__')\n"
            "        except SystemExit as exc:\n"
            "            raise AssertionError(f'exited {exc.code}')\n"
            "assert gui_called, 'gui.main was not called'\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", dispatch_test],
            cwd=repo_root,
            env=self.fake_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_python_m_venvdiscover(self) -> None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        res = subprocess.run(
            [sys.executable, "-m", "venvdiscover", "--help"],
            cwd=repo_root,
            env=self.fake_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("VenvDiscover", res.stdout)
        self.assertIn("--json", res.stdout)
        self.assertIn("--known-only", res.stdout)


if __name__ == "__main__":
    unittest.main()
