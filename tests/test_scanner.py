from __future__ import annotations

import os
import queue
import shutil
import time
import unittest
from unittest.mock import patch

from tests import fakes
from venvdiscover import packages, sizes
from venvdiscover.model import Env, Kind, norm_key
from venvdiscover.scanner import (
    EnvFound,
    EnvGone,
    EnvUpdated,
    Progress,
    ScanDone,
    Scanner,
    SizesDone,
)
from venvdiscover.store import Settings


class ScannerTests(unittest.TestCase):
    def test_scan_end_to_end(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "drives", "D")

            # 1. Plain venv
            venv1 = fakes.make_venv(tree, "venv1", packages={"requests": "2.31.0"})

            # 2. Conda root and conda env
            c_root = fakes.make_conda_root(tree, "miniconda3")
            c_env = fakes.make_conda_env(c_root, "env_data", conda_packages={"numpy": "1.26.0"})

            # 3. Known env listed in environments.txt to test Phase 2 + Phase 3 de-duplication
            fakes.write_file(places.environments_txt, f"{c_env}\n")

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()

            events = []
            deadline = time.monotonic() + 10.0
            scan_done = None

            # Wait for ScanDone
            while time.monotonic() < deadline:
                try:
                    ev = out_q.get(timeout=0.1)
                    events.append(ev)
                    if isinstance(ev, ScanDone):
                        scan_done = ev
                        break
                except queue.Empty:
                    continue

            self.assertIsNotNone(scan_done, "Scanner did not emit ScanDone within 10s")
            self.assertGreater(scan_done.seconds, 0.0)
            self.assertEqual(scan_done.total, 3)

            # Wait until every found env has an EnvUpdated and SizesDone arrives
            found_events = [e for e in events if isinstance(e, EnvFound)]
            self.assertEqual(len(found_events), 3, "Expected exactly 3 EnvFound events without duplicates")

            found_keys = {e.env.key for e in found_events}
            updated_keys = {e.env.key for e in events if isinstance(e, EnvUpdated)}
            has_sizes_done = any(isinstance(e, SizesDone) for e in events)

            while time.monotonic() < deadline and (len(updated_keys) < 3 or not has_sizes_done):
                try:
                    ev = out_q.get(timeout=0.1)
                    events.append(ev)
                    if isinstance(ev, EnvUpdated):
                        updated_keys.add(ev.env.key)
                    elif isinstance(ev, SizesDone):
                        has_sizes_done = True
                except queue.Empty:
                    continue

            scanner.join(timeout=2.0)

            self.assertEqual(len(found_keys), 3)
            self.assertEqual(updated_keys, found_keys)
            self.assertTrue(has_sizes_done, "SizesDone must arrive after all sizes are measured")

            updated_events = [e for e in events if isinstance(e, EnvUpdated)]
            self.assertEqual(len(updated_events), 3)
            for e in updated_events:
                self.assertIsNotNone(e.env.size)
                self.assertGreater(e.env.size, 0)

            # Assert SizesDone arrives after every EnvUpdated
            sizes_done_idx = [i for i, e in enumerate(events) if isinstance(e, SizesDone)][0]
            for i, e in enumerate(events):
                if isinstance(e, EnvUpdated):
                    self.assertLess(i, sizes_done_idx, "EnvUpdated must arrive before SizesDone")

    def test_scan_reuses_cache(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            fakes.make_venv(tree, "myvenv", packages={"flask": "3.0.0"})

            out_q1: queue.Queue = queue.Queue()
            scanner1 = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q1,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner1.start()

            cached_envs: list[Env] = []
            while len(cached_envs) < 1:
                ev = out_q1.get(timeout=10.0)
                if isinstance(ev, EnvUpdated):
                    cached_envs.append(ev.env)
            scanner1.join(2.0)

            self.assertEqual(len(cached_envs), 1)
            self.assertIsNotNone(cached_envs[0].size)
            self.assertGreater(cached_envs[0].size, 0)

            # Second scan with cached_envs, disk unchanged
            out_q2: queue.Queue = queue.Queue()
            scanner2 = Scanner(
                places=places,
                settings=Settings(),
                cached=cached_envs,
                out=out_q2,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )

            with patch("venvdiscover.packages.fill", wraps=packages.fill) as mock_fill:
                scanner2.start()
                events2 = []
                while True:
                    ev = out_q2.get(timeout=10.0)
                    events2.append(ev)
                    if isinstance(ev, ScanDone):
                        break
                scanner2.join(5.0)

                # Drain whatever is left in out_q2
                while not out_q2.empty():
                    events2.append(out_q2.get_nowait())

                mock_fill.assert_not_called()

            found2 = [e for e in events2 if isinstance(e, EnvFound)]
            self.assertEqual(len(found2), 1)
            self.assertEqual(found2[0].env.packages[0].name, "flask")
            self.assertEqual(found2[0].env.size, cached_envs[0].size)

            # Review Focus 4: Unchanged cached envs must not post EnvUpdated
            updated2 = [e for e in events2 if isinstance(e, EnvUpdated)]
            self.assertEqual(updated2, [], "Unchanged cached envs must not post EnvUpdated")

    def test_cached_last_used_updates(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            v_path = fakes.make_venv(tree, "v_used", packages={"pkgA": "1.0"})

            sp = os.path.join(v_path, "Lib", "site-packages")
            cfg = os.path.join(v_path, "pyvenv.cfg")
            py_exe = os.path.join(v_path, "Scripts", "python.exe")

            t_old = 1700000000.0
            os.utime(packages.longpath(sp), (t_old, t_old))
            os.utime(packages.longpath(cfg), (t_old, t_old))
            os.utime(packages.longpath(py_exe), (t_old, t_old))

            # Stored in cache with old last_used
            cached_env = Env(
                path=v_path,
                kind=Kind.VENV,
                name="v_used",
                size=300,
                last_used=t_old,
            )
            # Ensure stamp matches the folder so fill is not called
            cached_env.stamp = packages.package_stamp(cached_env)

            # Simulate python.exe execution by updating atime of python.exe
            t_used = 1790000000.0
            os.utime(packages.longpath(py_exe), (t_used, t_old))

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[cached_env],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )

            with patch("venvdiscover.packages.fill", wraps=packages.fill) as mock_fill:
                scanner.start()
                events = []
                while True:
                    ev = out_q.get(timeout=10.0)
                    events.append(ev)
                    if isinstance(ev, ScanDone):
                        break
                scanner.join(2.0)

                mock_fill.assert_not_called()

            found = [e for e in events if isinstance(e, EnvFound)]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].env.last_used, t_used)

    def test_known_places_outside_roots_reported(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree_d = os.path.join(tmp, "drives", "D")
            tree_c = os.path.join(tmp, "drives", "C")
            c_root = fakes.make_conda_root(tree_c, "miniconda3")
            c_env = fakes.make_conda_env(c_root, "outside_env", conda_packages={"scipy": "1.11.0"})

            # List c_env in environments.txt
            fakes.write_file(places.environments_txt, f"{c_env}\n")

            out_q: queue.Queue = queue.Queue()
            # Scan only tree_d (tree_c is outside roots)
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tree_d],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()

            events = []
            while True:
                ev = out_q.get(timeout=10.0)
                events.append(ev)
                if isinstance(ev, ScanDone):
                    break
            scanner.join(2.0)

            found = [e for e in events if isinstance(e, EnvFound)]
            found_paths = [e.env.path for e in found]
            self.assertIn(c_env, found_paths, "Conda env in environments.txt outside roots must be found in Phase 2")

    def test_app_dir_skipped_in_walk(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = places.localappdata
            # Create a fake venv in the bin under app_dir
            bin_venv = fakes.make_venv(os.path.join(places.app_dir, "bin", "item1"), "env")
            normal_venv = fakes.make_venv(tree, "normal_venv")

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()

            events = []
            while True:
                ev = out_q.get(timeout=10.0)
                events.append(ev)
                if isinstance(ev, ScanDone):
                    break
            scanner.join(2.0)

            found_keys = {e.env.key for e in events if isinstance(e, EnvFound)}
            self.assertIn(Env(normal_venv, Kind.VENV, "normal_venv").key, found_keys)
            self.assertNotIn(Env(bin_venv, Kind.VENV, "env").key, found_keys, "Envs under places.app_dir must be skipped")

    def test_scan_gone(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            deleted_path = os.path.join(tree, "deleted_venv")
            fakes.make_venv(tree, "deleted_venv")

            deleted_env = Env(
                path=deleted_path,
                kind=Kind.VENV,
                name="deleted_venv",
                size=500,
            )

            # Now delete it from disk
            shutil.rmtree(deleted_path)

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[deleted_env],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()

            gone_events = []
            while True:
                ev = out_q.get(timeout=10.0)
                if isinstance(ev, EnvGone):
                    gone_events.append(ev)
                elif isinstance(ev, ScanDone):
                    break
            scanner.join(2.0)

            self.assertEqual(len(gone_events), 1)
            self.assertEqual(gone_events[0].key, deleted_env.key)

    def test_scan_not_seen_but_exists_not_gone(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            unscanned_tree = os.path.join(tmp, "other_drive")
            existing_venv = fakes.make_venv(unscanned_tree, "existing_venv")

            cached_env = Env(
                path=existing_venv,
                kind=Kind.VENV,
                name="existing_venv",
                size=500,
            )

            out_q: queue.Queue = queue.Queue()
            # Scan only 'tree', so 'other_drive' is not visited, but folder exists
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[cached_env],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()

            gone_events = []
            while True:
                ev = out_q.get(timeout=10.0)
                if isinstance(ev, EnvGone):
                    gone_events.append(ev)
                elif isinstance(ev, ScanDone):
                    break
            scanner.join(2.0)

            self.assertEqual(len(gone_events), 0, "Env whose folder exists must not produce EnvGone")

    def test_scan_stamp_changed_triggers_fill(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            v_path = fakes.make_venv(tree, "v_stamp", packages={"pkgA": "1.0"})

            # Stale cached env with older stamp
            cached_env = Env(
                path=v_path,
                kind=Kind.VENV,
                name="v_stamp",
                size=200,
                stamp=100.0,
            )

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[cached_env],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )

            with patch("venvdiscover.packages.fill", wraps=packages.fill) as mock_fill:
                scanner.start()
                events = []
                while True:
                    ev = out_q.get(timeout=10.0)
                    events.append(ev)
                    if isinstance(ev, ScanDone):
                        break
                scanner.join(2.0)

                mock_fill.assert_called_once()

            # Wait for EnvUpdated
            deadline = time.monotonic() + 5.0
            updated = [e for e in events if isinstance(e, EnvUpdated)]
            while time.monotonic() < deadline and not updated:
                try:
                    ev = out_q.get(timeout=0.1)
                    if isinstance(ev, EnvUpdated):
                        updated.append(ev)
                except queue.Empty:
                    continue
            self.assertEqual(len(updated), 1)

    def test_progress_rate_limiting(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tmp],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )

            # First progress call should succeed
            scanner._on_progress(r"C:\fake", 2000, r"C:\fake\sub")
            self.assertFalse(out_q.empty())
            p1 = out_q.get_nowait()
            self.assertIsInstance(p1, Progress)
            self.assertEqual(p1.text, "Scanning C:\\fake \u2014 2,000 folders")

            # Immediate second call (<0.25s) should be dropped by rate limiter
            scanner._on_progress(r"C:\fake", 4000, r"C:\fake\sub2")
            self.assertTrue(out_q.empty())

            # After 0.26s delay, progress should be emitted
            time.sleep(0.26)
            scanner._on_progress(r"C:\fake", 6000, r"C:\fake\sub3")
            self.assertFalse(out_q.empty())
            p2 = out_q.get_nowait()
            self.assertIsInstance(p2, Progress)
            self.assertEqual(p2.text, "Scanning C:\\fake \u2014 6,000 folders")

    def test_scan_stop(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            for i in range(20):
                fakes.make_venv(tree, f"venv_{i}")

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()
            scanner.stop()
            scanner.join(timeout=2.0)

            # Check that all worker threads are stopped
            for t in getattr(scanner, "_threads", []):
                self.assertFalse(t.is_alive(), f"Thread {t.name} is still alive after stop() and join(2)")

            # Check that a stopped scan never posts SizesDone
            events = []
            while not out_q.empty():
                events.append(out_q.get_nowait())
            self.assertFalse(any(isinstance(e, SizesDone) for e in events), "Stopped scan must never post SizesDone")

    def test_scanner_conda_base_skips_envs(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            c_root = fakes.make_conda_root(tmp, "miniconda_base")
            # 1000-byte file in envs\x
            fakes.write_file(os.path.join(c_root, "envs", "x", "dummy.bin"), b"x" * 1000)
            # A 200-byte file in root
            fakes.write_file(os.path.join(c_root, "root_file.bin"), b"y" * 200)

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tmp],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )
            scanner.start()
            events = []
            while True:
                ev = out_q.get(timeout=10.0)
                events.append(ev)
                if isinstance(ev, SizesDone):
                    break
            scanner.join(2.0)

            updated = [e for e in events if isinstance(e, EnvUpdated) and e.env.kind == Kind.CONDA_BASE]
            self.assertEqual(len(updated), 1)
            # Size must exclude the 1000 bytes in envs\
            self.assertLess(updated[0].env.size, 1000)

    def test_no_sizes_done_when_stopped_during_sizes(self) -> None:
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            tree = os.path.join(tmp, "tree")
            fakes.make_venv(tree, "venv_1")
            fakes.make_venv(tree, "venv_2")

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[tree],
                registry_paths=lambda: [],
                environ={},  # never the real environment: CI runners set PIPX_HOME and more
            )

            orig_folder_size = sizes.folder_size

            def slow_folder_size(*args, **kwargs):
                time.sleep(0.1)
                return orig_folder_size(*args, **kwargs)

            with patch("venvdiscover.sizes.folder_size", side_effect=slow_folder_size):
                scanner.start()
                # Wait until ScanDone
                while True:
                    ev = out_q.get(timeout=10.0)
                    if isinstance(ev, ScanDone):
                        break
                # Sizes are still being measured due to slow_folder_size. Stop now.
                scanner.stop()
                scanner.join(timeout=2.0)

            # Drain any remaining events
            remaining = []
            while not out_q.empty():
                remaining.append(out_q.get_nowait())

            self.assertFalse(any(isinstance(e, SizesDone) for e in remaining), "Must not emit SizesDone when stopped during sizes")


    def test_scanner_pixi_mamba_and_registry_conda(self) -> None:
        """Scanner phase 2 discovers pixi envs, mamba envs without conda-meta (conda_root=None), and registry conda bases."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            scan_root = os.path.join(tmp, "scan_root")
            os.makedirs(scan_root, exist_ok=True)

            # 1. Pixi env under ~/.pixi/envs
            pixi_env = os.path.join(places.home, ".pixi", "envs", "pixi_proj")
            os.makedirs(os.path.join(pixi_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(pixi_env, "conda-meta", "pixi"), "pixi")

            # 2. Mamba env under %LOCALAPPDATA%\mamba\envs (mamba root has NO conda-meta)
            mamba_env = os.path.join(places.localappdata, "mamba", "envs", "mamba_env")
            os.makedirs(os.path.join(mamba_env, "conda-meta"), exist_ok=True)
            fakes.write_file(os.path.join(mamba_env, "conda-meta", "history"), "history")

            # 3. Injected registry conda base
            reg_conda = os.path.join(tmp, "reg_miniconda")
            os.makedirs(os.path.join(reg_conda, "conda-meta"), exist_ok=True)
            os.makedirs(os.path.join(reg_conda, "condabin"), exist_ok=True)
            fakes.write_file(os.path.join(reg_conda, "conda-meta", "history"), "history")

            out_q: queue.Queue = queue.Queue()
            scanner = Scanner(
                places=places,
                settings=Settings(),
                cached=[],
                out=out_q,
                roots=[scan_root],
                registry_paths=lambda: [reg_conda],
            )
            scanner.start()
            envs: list[Env] = []
            while True:
                ev = out_q.get(timeout=10.0)
                if isinstance(ev, EnvFound):
                    envs.append(ev.env)
                elif isinstance(ev, ScanDone):
                    break
            scanner.join(timeout=2.0)

            # Assert pixi env found as Kind.CONDA with conda_root=None
            p_found = next((e for e in envs if norm_key(e.path) == norm_key(pixi_env)), None)
            self.assertIsNotNone(p_found)
            self.assertEqual(p_found.kind, Kind.CONDA)
            self.assertIsNone(p_found.conda_root)

            # Assert mamba env found as Kind.CONDA with conda_root=None
            m_found = next((e for e in envs if norm_key(e.path) == norm_key(mamba_env)), None)
            self.assertIsNotNone(m_found)
            self.assertEqual(m_found.kind, Kind.CONDA)
            self.assertIsNone(m_found.conda_root)

            # Assert registry conda base found as Kind.CONDA_BASE
            r_found = next((e for e in envs if norm_key(e.path) == norm_key(reg_conda)), None)
            self.assertIsNotNone(r_found)
            self.assertEqual(r_found.kind, Kind.CONDA_BASE)

