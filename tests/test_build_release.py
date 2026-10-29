"""Tests for the release builder's pure parts (scripts/build_release.py). PyInstaller itself is not run here."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import scripts.build_release as br


class TestBuildRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="test_br_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, rel: str, data: bytes | str) -> str:
        path = os.path.join(self.tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def test_private_notes_are_excluded_from_the_source(self) -> None:
        self.assertTrue(br.is_excluded("docs/design.md"))
        self.assertTrue(br.is_excluded("docs/notes/plans/plan.md"))
        self.assertTrue(br.is_excluded("docs\\notes\\x.md"))
        for kept in ("docs/TESTING.md", "docs/guide/how-to-use.en.html", "docs/design.md.txt", "README.md",
                     "venvdiscover/ui/app.py"):
            self.assertFalse(br.is_excluded(kept), kept)

    def test_private_markers_come_from_the_environment(self) -> None:
        marks = br.private_markers({"USERPROFILE": r"C:\Users\Kim", "USERNAME": "Kim"})
        self.assertEqual(marks, [r"C:\Users\Kim", "Kim"])
        self.assertEqual(br.private_markers({"USERNAME": "ab"}), [])  # too short to be meaningful
        self.assertEqual(br.private_markers({}), [])

    def test_find_private_mentions_reads_text_files_only(self) -> None:
        self._write("scripts/a.py", 'P = r"c:\\users\\kim\\miniconda\\python.exe"\n')
        self._write("docs/clean.md", "nothing personal here\n")
        self._write("venvdiscover/assets/logo.png", b"\x89PNG Kim C:\\Users\\Kim")
        hits = br.find_private_mentions(self.tmp, [r"C:\Users\Kim", "Kim"])
        self.assertEqual(hits, [("scripts/a.py", r"C:\Users\Kim"), ("scripts/a.py", "Kim")])

    def test_version_resource_is_a_valid_literal(self) -> None:
        text = br.version_file_text("1.0", (1, 0, 0, 0))
        ast.parse(text)
        self.assertIn("filevers=(1, 0, 0, 0)", text)
        self.assertIn("StringStruct('FileVersion', '1.0.0.0')", text)
        self.assertIn("StringStruct('ProductName', 'Venv Discover')", text)
        self.assertIn("StringStruct('ProductVersion', '1.0')", text)
        self.assertIn("StringStruct('OriginalFilename', 'VenvDiscover.exe')", text)

        v11_text = br.version_file_text("1.1", (1, 1, 0, 0))
        ast.parse(v11_text)
        self.assertIn("filevers=(1, 1, 0, 0)", v11_text)
        self.assertIn("StringStruct('FileVersion', '1.1.0.0')", v11_text)
        self.assertIn("StringStruct('ProductVersion', '1.1')", v11_text)
        self.assertIn("StringStruct('OriginalFilename', 'VenvDiscover.exe')", v11_text)

    def test_file_version_parsing(self) -> None:
        cases = [
            ("1.1", (1, 1, 0, 0)),
            ("1.1.1", (1, 1, 1, 0)),
            ("1.2.3.4", (1, 2, 3, 4)),
        ]
        for ver_str, expected in cases:
            fv = br.parse_file_version(ver_str)
            self.assertEqual(fv, expected)
            text = br.version_file_text(ver_str, fv)
            ast.parse(text)
            self.assertIn(f"filevers={expected}", text)
            self.assertIn(f"StringStruct('ProductVersion', '{ver_str}')", text)

    def test_zip_is_byte_identical_and_sums_cover_every_file(self) -> None:
        pkg = os.path.join(self.tmp, "pkg")
        for rel, body in (("VenvDiscover.exe", b"MZ fake"), ("How to use (EN).html", "<html></html>"),
                          ("source/README.md", "# hi\n"), ("source/venvdiscover/app.py", "x = 1\n")):
            path = os.path.join(pkg, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(body if isinstance(body, bytes) else body.encode("utf-8"))
        sums = br.write_sums(pkg)
        with open(sums, encoding="utf-8") as f:
            listed = [line.split("  ", 1)[1] for line in f.read().splitlines()]
        self.assertEqual(listed, ["How to use (EN).html", "VenvDiscover.exe", "source/README.md",
                                  "source/venvdiscover/app.py"])
        z1, z2 = os.path.join(self.tmp, "a.zip"), os.path.join(self.tmp, "b.zip")
        stamp = (2026, 10, 27, 12, 0, 0)
        br.zip_dir(pkg, z1, "VenvDiscover-1.0", stamp)
        br.zip_dir(pkg, z2, "VenvDiscover-1.0", stamp)
        with open(z1, "rb") as f1, open(z2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())
        with zipfile.ZipFile(z1) as z:
            names = z.namelist()
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(n.startswith("VenvDiscover-1.0/") for n in names))
        self.assertIn("VenvDiscover-1.0/SHA256SUMS.txt", names)

    def test_package_docs_exist(self) -> None:
        for src in br.PACKAGE_DOCS:
            self.assertTrue(os.path.isfile(os.path.join(br.REPO_ROOT, *src.split("/"))), src)

    def test_shared_source_has_no_private_mentions(self) -> None:
        """Every tracked text file that ships in the zip is free of this PC's user profile and user name."""
        markers = br.private_markers()
        if not markers:
            self.skipTest("no USERPROFILE/USERNAME in this environment")
        tracked = subprocess.check_output(["git", "ls-files"], cwd=br.REPO_ROOT, text=True).splitlines()
        shipped = [p for p in tracked if not br.is_excluded(p) and p.lower().endswith(br.TEXT_EXTENSIONS)]
        for rel in shipped:
            src = os.path.join(br.REPO_ROOT, *rel.split("/"))
            if os.path.isfile(src):
                dst = os.path.join(self.tmp, "src", *rel.split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
        self.assertEqual(br.find_private_mentions(os.path.join(self.tmp, "src"), markers), [])


if __name__ == "__main__":
    unittest.main()
