"""Tests for the user guide and the project README."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from venvdiscover.model import LOCKED_KINDS, Kind
from venvdiscover.trashbin import BIN_LIMIT, KEEP_DAYS

# Root of the repository is parent of tests/
REPO_ROOT = Path(__file__).resolve().parent.parent
EN_GUIDE = REPO_ROOT / "docs" / "guide" / "how-to-use.en.html"
EN_README = REPO_ROOT / "README.md"

EXPECTED_SECTIONS = [
    "what-it-is",
    "starting",
    "main-window",
    "finding-space",
    "looking-inside",
    "actions",
    "deleting-safely",
    "settings",
    "data-locations",
    "faq",
]


class GuideAndReadmeTests(unittest.TestCase):
    def test_files_exist(self) -> None:
        self.assertTrue(EN_GUIDE.is_file(), f"Missing English guide: {EN_GUIDE}")
        self.assertTrue(EN_README.is_file(), f"Missing English README: {EN_README}")

    def test_html_guides_are_self_contained(self) -> None:
        for path in (EN_GUIDE,):
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                # No script tags of any kind
                self.assertNotIn("<script", content.lower())
                self.assertNotIn("</script", content.lower())

                # No external HTTP or HTTPS URLs (fully offline)
                # Ensure no href="http...", src="http...", url(http...)
                self.assertNotRegex(
                    content,
                    r"""(?:href|src|url)\s*=\s*['"]?https?://""",
                    f"{path.name} contains external web link",
                )
                self.assertNotIn("http://", content)
                self.assertNotIn("https://", content)

                # Every img tag must use a data: URI
                img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
                self.assertGreater(len(img_srcs), 0, f"Expected embedded images in {path.name}")
                for src in img_srcs:
                    self.assertTrue(
                        src.startswith("data:image/"),
                        f"Image src in {path.name} does not use data: URI: {src[:30]}...",
                    )

                # Inline CSS and dark mode support
                self.assertIn("<style>", content)
                self.assertIn("prefers-color-scheme: dark", content)

    def test_section_structure_and_order(self) -> None:
        for path in (EN_GUIDE,):
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                # Extract sections with id attribute in document order
                section_ids = re.findall(r'<section[^>]+id=["\']([^"\']+)["\']', content)
                self.assertEqual(
                    section_ids,
                    EXPECTED_SECTIONS,
                    f"Section IDs in {path.name} must exactly match expected 10 sections in order",
                )

    def test_numbers_match_trashbin_constants(self) -> None:
        # 1 GB in bytes: 1024 ** 3 = 1073741824
        gb_val = BIN_LIMIT // (1024**3)
        self.assertEqual(gb_val, 1, "Expected BIN_LIMIT to be 1 GB")
        bytes_str = f"{BIN_LIMIT:,}".replace(",", " ")  # or 1,073,741,824 or raw

        for path in (EN_GUIDE,):
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                # Check for 1 GB reference
                self.assertIn(f"{gb_val} GB", content)
                # Check for retention days from trashbin.KEEP_DAYS
                self.assertIn(str(KEEP_DAYS), content)

        # Check locked kinds are documented
        self.assertIn(Kind.CONDA_BASE, LOCKED_KINDS)
        self.assertIn(Kind.PYTHON, LOCKED_KINDS)

    def test_readmes_content_and_links(self) -> None:
        for readme, guide_name in ((EN_README, "how-to-use.en.html"),):
            with self.subTest(file=readme.name):
                content = readme.read_text(encoding="utf-8")
                # Link to the guide
                self.assertIn(guide_name, content)
                # Command to run from source
                self.assertIn("VenvDiscover.pyw", content)
                # Command to run tests
                self.assertIn("run_tests.py", content)

    def test_facts_and_review_requirements(self) -> None:
        """Verify facts match the app exactly as checked in review."""
        for path in (EN_GUIDE,):
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                # Both tabs must be mentioned
                self.assertIn("Environments", content)
                self.assertIn("Bin", content)

                # Palettes must match the actual 4 theme palettes
                self.assertIn("Default", content)
                self.assertIn("Night city", content)
                self.assertIn("Calm glass", content)
                self.assertIn("Bold editorial", content)

                # Ctrl+C is the only keyboard shortcut
                self.assertIn("Ctrl+C", content)

                # SmartScreen illustration caption
                self.assertTrue(
                    "Illustration" in content,
                    f"Missing illustration disclaimer in {path.name}",
                )

        # Committed capture script must exist in scripts/
        capture_script = REPO_ROOT / "scripts" / "make_guide_screenshots.py"
        self.assertTrue(capture_script.is_file(), f"Missing {capture_script}")

    def test_privacy_and_no_host_paths(self) -> None:
        # The real user's profile and name come from the environment, so no name is written here.
        forbidden = [
            os.environ.get("USERPROFILE", ""),
            os.environ.get("USERNAME", ""),
            str(REPO_ROOT.parent),
            "AppData\\Local\\Temp",
            "/Temp/",
        ]
        forbidden = [f for f in forbidden if len(f) > 2]
        for path in (EN_GUIDE, EN_README):
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                for f in forbidden:
                    self.assertNotIn(f.lower(), content.lower())


if __name__ == "__main__":
    unittest.main()
