"""Tests for the application logo, multi-size ICO asset, and taskbar icon wiring."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from tests.tk_support import create_test_app
from venvdiscover.places import Places
from venvdiscover.ui import app as app_module
from venvdiscover.ui.app import App, main
import scripts.make_icon as make_icon

EXPECTED_SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256]


class TestUiIcon(unittest.TestCase):
    """Validate app logo assets, make_icon generator, and app icon wiring."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.ico_path = os.path.join(cls.repo_root, "venvdiscover", "assets", "venvdiscover.ico")
        cls.png_path = os.path.join(cls.repo_root, "venvdiscover", "assets", "venvdiscover-256.png")
        cls.sheet_path = os.path.join(cls.repo_root, "docs", "logo-preview.png")

    def test_ico_header_and_sizes(self) -> None:
        """The .ico must have a valid type 1 header and contain all 14 required sizes."""
        self.assertTrue(os.path.isfile(self.ico_path), f"Missing {self.ico_path}")
        with open(self.ico_path, "rb") as f:
            data = f.read()

        reserved, ico_type, count = struct.unpack("<HHH", data[:6])
        self.assertEqual(reserved, 0)
        self.assertEqual(ico_type, 1, "Must be valid ICO type 1")
        self.assertEqual(count, len(EXPECTED_SIZES), f"Expected {len(EXPECTED_SIZES)} entries, got {count}")

        entries_sizes = []
        for i in range(count):
            offset = 6 + i * 16
            w, h, color_count, res, planes, bpp, size_bytes, img_offset = struct.unpack(
                "<BBBBHHII", data[offset : offset + 16]
            )
            width = 256 if w == 0 else w
            height = 256 if h == 0 else h
            self.assertEqual(width, height)
            entries_sizes.append(width)

            # Each entry must be a valid PNG
            img_data = data[img_offset : img_offset + size_bytes]
            self.assertTrue(img_data.startswith(b"\x89PNG\r\n\x1a\n"), f"Entry {width}px is not a PNG")
            # Verify IHDR chunk width and height
            ihdr_w, ihdr_h = struct.unpack(">II", img_data[16:24])
            self.assertEqual(ihdr_w, width)
            self.assertEqual(ihdr_h, height)

        self.assertEqual(sorted(entries_sizes), EXPECTED_SIZES)

    def test_png256_and_contact_sheet_exist(self) -> None:
        """venvdiscover-256.png and docs/logo-preview.png must exist and be valid PNGs."""
        for p in (self.png_path, self.sheet_path):
            self.assertTrue(os.path.isfile(p), f"Missing {p}")
            with open(p, "rb") as f:
                header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n", f"{p} is not a valid PNG")

    def test_generator_deterministic_and_matches_committed(self) -> None:
        """Running make_icon.generate_all twice produces byte-identical committed files."""
        temp_dir1 = tempfile.mkdtemp(prefix="vd_icon_test1_")
        temp_dir2 = tempfile.mkdtemp(prefix="vd_icon_test2_")
        try:
            make_icon.generate_all(temp_dir1)
            make_icon.generate_all(temp_dir2)

            for rel in [
                os.path.join("venvdiscover", "assets", "venvdiscover.ico"),
                os.path.join("venvdiscover", "assets", "venvdiscover-256.png"),
                os.path.join("docs", "logo-preview.png"),
            ]:
                p1 = os.path.join(temp_dir1, rel)
                p2 = os.path.join(temp_dir2, rel)
                committed = os.path.join(self.repo_root, rel)

                with open(p1, "rb") as f1, open(p2, "rb") as f2, open(committed, "rb") as fc:
                    b1 = f1.read()
                    b2 = f2.read()
                    bc = fc.read()

                self.assertEqual(b1, b2, f"Run 1 and Run 2 differ for {rel}")
                self.assertEqual(b1, bc, f"Generated file differs from committed {rel}")
        finally:
            shutil.rmtree(temp_dir1, ignore_errors=True)
            shutil.rmtree(temp_dir2, ignore_errors=True)

    def test_app_calls_iconbitmap_with_default(self) -> None:
        """App.__init__ must call iconbitmap(default=...) with an existing .ico path."""
        calls: list[dict] = []
        orig_iconbitmap = App.iconbitmap

        def mock_iconbitmap(self: App, *args: any, **kwargs: any) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            try:
                orig_iconbitmap(self, *args, **kwargs)
            except Exception:
                pass

        with patch.object(App, "iconbitmap", mock_iconbitmap):
            places = Places.from_environ()
            app = create_test_app(places)
            try:
                self.assertTrue(len(calls) >= 1, "iconbitmap was not called")
                kw = calls[0]["kwargs"]
                self.assertIn("default", kw, "iconbitmap must be called with default= keyword argument")
                default_path = kw["default"]
                self.assertTrue(os.path.isabs(default_path), f"Path must be absolute: {default_path}")
                self.assertTrue(os.path.isfile(default_path), f"Icon file must exist: {default_path}")
                self.assertTrue(default_path.endswith("venvdiscover.ico"))
            finally:
                app.destroy()

    def test_app_builds_when_icon_missing(self) -> None:
        """App.__init__ must succeed even if the icon file is missing or corrupted."""
        with patch("os.path.exists", return_value=False):
            places = Places.from_environ()
            app = create_test_app(places)
            try:
                self.assertIsInstance(app, App)
            finally:
                app.destroy()

    def test_main_call_order(self) -> None:
        """main() call order must be: AppUserModelID -> App -> mainloop."""
        call_order: list[str] = []

        def mock_app_id(app_id: str) -> None:
            call_order.append(f"app_id:{app_id}")

        mock_app_instance = MagicMock()

        def mock_mainloop() -> None:
            call_order.append("mainloop")

        mock_app_instance.mainloop = mock_mainloop

        def mock_app_class(*args: any, **kwargs: any) -> MagicMock:
            call_order.append("App")
            return mock_app_instance

        with patch("ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID", side_effect=mock_app_id), \
             patch("venvdiscover.ui.app.App", side_effect=mock_app_class):
            main()

        expected = ["app_id:VenvDiscover.App", "App", "mainloop"]
        self.assertEqual(call_order, expected, f"Incorrect main() sequence: {call_order}")

    def test_building_app_alone_never_calls_appusermodelid(self) -> None:
        """Building App (e.g. during test suites) must NEVER call SetCurrentProcessExplicitAppUserModelID."""
        with patch("ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID") as mock_set_id:
            places = Places.from_environ()
            app = create_test_app(places)
            try:
                mock_set_id.assert_not_called()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
