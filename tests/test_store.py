"""Tests for venvdiscover.store."""

import json
import os
import time
import unittest
from unittest import mock

from tests import fakes
from venvdiscover.model import Env, Kind, Package
from venvdiscover.store import (
    Settings,
    append_log,
    load_cache,
    load_settings,
    save_cache,
    save_settings,
)


class StoreTests(unittest.TestCase):
    def test_cache_roundtrip(self) -> None:
        with fakes.tmpdir() as tmp:
            cache_file = os.path.join(tmp, "cache.json")
            pkg1 = Package(name="requests", version="2.31.0", source="pip")
            pkg2 = Package(name="numpy", version="1.26.0", source="conda")

            envs = [
                Env(
                    path=os.path.join(tmp, "venv1"),
                    kind=Kind.VENV,
                    name="venv1",
                    python_version="3.12.4",
                    packages=[pkg1],
                    size=1024,
                    last_used=1793000000.0,
                    stamp=1793000050.0,
                ),
                Env(
                    path=os.path.join(tmp, "conda_root"),
                    kind=Kind.CONDA_BASE,
                    name="conda_root",
                    python_version="3.11.9",
                    packages=[pkg2],
                    size=2048,
                    last_used=1793000100.0,
                    stamp=1793000150.0,
                ),
            ]

            save_cache(cache_file, envs)
            self.assertTrue(os.path.isfile(cache_file))

            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.assertEqual(raw.get("version"), 1)
            self.assertIsInstance(raw.get("envs"), list)

            loaded = load_cache(cache_file)
            self.assertEqual(len(loaded), len(envs))
            for orig, got in zip(envs, loaded):
                self.assertEqual(orig.path, got.path)
                self.assertEqual(orig.kind, got.kind)
                self.assertEqual(orig.name, got.name)
                self.assertEqual(orig.python_version, got.python_version)
                self.assertEqual(orig.packages, got.packages)
                self.assertEqual(orig.size, got.size)
                self.assertEqual(orig.last_used, got.last_used)
                self.assertEqual(orig.stamp, got.stamp)
                self.assertEqual(orig.key, got.key)

    def test_cache_corrupt(self) -> None:
        with fakes.tmpdir() as tmp:
            missing_file = os.path.join(tmp, "nonexistent.json")
            self.assertEqual(load_cache(missing_file), [])

            empty_file = os.path.join(tmp, "empty.json")
            fakes.write_file(empty_file, "")
            self.assertEqual(load_cache(empty_file), [])

            bad_json = os.path.join(tmp, "bad.json")
            fakes.write_file(bad_json, "{oops")
            self.assertEqual(load_cache(bad_json), [])

            wrong_version = os.path.join(tmp, "wrong_ver.json")
            fakes.write_file(wrong_version, json.dumps({"version": 2, "envs": []}))
            self.assertEqual(load_cache(wrong_version), [])

            not_dict = os.path.join(tmp, "not_dict.json")
            fakes.write_file(not_dict, json.dumps([1, 2, 3]))
            self.assertEqual(load_cache(not_dict), [])

            corrupt_envs = os.path.join(tmp, "corrupt_envs.json")
            fakes.write_file(corrupt_envs, json.dumps({"version": 1, "envs": ["not_a_dict"]}))
            self.assertEqual(load_cache(corrupt_envs), [])

    def test_log_append(self) -> None:
        with fakes.tmpdir() as tmp:
            log_file = os.path.join(tmp, "logs", "actions.log")
            t_before = time.time()
            append_log(log_file, "move_to_bin", os.path.join(tmp, "env1"), "binned")
            append_log(log_file, "restore", os.path.join(tmp, "env2"))
            t_after = time.time()

            self.assertTrue(os.path.isfile(log_file))
            with open(log_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

            self.assertEqual(len(lines), 2)
            entry1 = json.loads(lines[0])
            self.assertEqual(entry1["action"], "move_to_bin")
            self.assertEqual(entry1["env_path"], os.path.join(tmp, "env1"))
            self.assertEqual(entry1["detail"], "binned")
            self.assertGreaterEqual(entry1["ts"], t_before - 1.0)
            self.assertLessEqual(entry1["ts"], t_after + 1.0)

            entry2 = json.loads(lines[1])
            self.assertEqual(entry2["action"], "restore")
            self.assertEqual(entry2["env_path"], os.path.join(tmp, "env2"))
            self.assertEqual(entry2["detail"], "")
            self.assertGreaterEqual(entry2["ts"], entry1["ts"])

    def test_settings_roundtrip(self) -> None:
        with fakes.tmpdir() as tmp:
            settings_file = os.path.join(tmp, "settings.json")
            s = Settings(drives=["C:\\", "D:\\"], terminal="powershell", theme="light", palette="night-city")
            save_settings(settings_file, s)

            loaded = load_settings(settings_file)
            self.assertEqual(loaded.drives, ["C:\\", "D:\\"])
            self.assertEqual(loaded.terminal, "powershell")
            self.assertEqual(loaded.theme, "light")
            self.assertEqual(loaded.palette, "night-city")

            s_none = Settings(drives=None, terminal="cmd", theme="dark", palette="default")
            save_settings(settings_file, s_none)
            loaded_none = load_settings(settings_file)
            self.assertIsNone(loaded_none.drives)
            self.assertEqual(loaded_none.terminal, "cmd")
            self.assertEqual(loaded_none.theme, "dark")
            self.assertEqual(loaded_none.palette, "default")

    def test_settings_missing_or_corrupt(self) -> None:
        with fakes.tmpdir() as tmp:
            missing = os.path.join(tmp, "missing_settings.json")
            def_s = load_settings(missing)
            self.assertEqual(def_s, Settings(drives=None, terminal="cmd", theme="dark", palette="default"))

            bad = os.path.join(tmp, "bad_settings.json")
            fakes.write_file(bad, "{bad json")
            self.assertEqual(load_settings(bad), Settings(drives=None, terminal="cmd", theme="dark", palette="default"))

            # Old settings without terminal/theme/palette gives defaults
            old_file = os.path.join(tmp, "old_settings.json")
            fakes.write_file(old_file, json.dumps({"drives": ["E:\\"]}))
            loaded_old = load_settings(old_file)
            self.assertEqual(loaded_old.drives, ["E:\\"])
            self.assertEqual(loaded_old.terminal, "cmd")
            self.assertEqual(loaded_old.theme, "dark")
            self.assertEqual(loaded_old.palette, "default")

    def test_atomic_save_cache(self) -> None:
        with fakes.tmpdir() as tmp:
            cache_file = os.path.join(tmp, "cache.json")
            old_envs = [
                Env(
                    path=os.path.join(tmp, "old_env"),
                    kind=Kind.VENV,
                    name="old_env",
                )
            ]
            save_cache(cache_file, old_envs)
            self.assertEqual(len(load_cache(cache_file)), 1)

            new_envs = [
                Env(
                    path=os.path.join(tmp, "new_env"),
                    kind=Kind.VENV,
                    name="new_env",
                )
            ]
            with mock.patch("venvdiscover.store.json.dump", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_cache(cache_file, new_envs)

            # Old cache remains completely intact
            loaded = load_cache(cache_file)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "old_env")

    def test_atomic_save_settings(self) -> None:
        with fakes.tmpdir() as tmp:
            settings_file = os.path.join(tmp, "settings.json")
            old_settings = Settings(drives=["C:\\"], terminal="powershell", theme="dark", palette="default")
            save_settings(settings_file, old_settings)
            self.assertEqual(load_settings(settings_file).drives, ["C:\\"])
            self.assertEqual(load_settings(settings_file).terminal, "powershell")
            self.assertEqual(load_settings(settings_file).theme, "dark")
            self.assertEqual(load_settings(settings_file).palette, "default")

            new_settings = Settings(drives=["D:\\", "E:\\"], terminal="gitbash", theme="light", palette="calm-glass")
            with mock.patch("venvdiscover.store.json.dump", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_settings(settings_file, new_settings)

            # Old settings remain completely intact
            loaded = load_settings(settings_file)
            self.assertEqual(loaded.drives, ["C:\\"])
            self.assertEqual(loaded.terminal, "powershell")
            self.assertEqual(loaded.theme, "dark")
            self.assertEqual(loaded.palette, "default")
