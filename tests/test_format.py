import unittest

from venvdiscover.ui.format import fmt_date, fmt_frees, fmt_shared_note, fmt_size


class FormatTests(unittest.TestCase):
    def test_fmt_frees_and_shared_note(self):
        gb = 1024 ** 3
        self.assertEqual(fmt_frees(3 * gb, gb), "1.0 GB *")
        self.assertEqual(fmt_frees(gb, gb), "1.0 GB")
        self.assertEqual(fmt_frees(gb, None), "1.0 GB")  # not measured yet: show the size on disk, no mark
        self.assertEqual(fmt_frees(None, None), "…")
        self.assertEqual(fmt_frees(gb, gb - 1024 ** 2), "1023.0 MB")  # 1 MB shared: too little to mark

        self.assertEqual(fmt_shared_note(gb, gb), "")
        self.assertEqual(fmt_shared_note(gb, None), "")
        self.assertEqual(
            fmt_shared_note(3 * gb, gb),
            "* 3.0 GB on disk, 2.0 GB shared with other folders (like a package cache): deleting frees 1.0 GB",
        )

    def test_fmt_size(self):
        self.assertEqual(fmt_size(None), "…")
        self.assertEqual(fmt_size(0), "0 B")
        self.assertEqual(fmt_size(512), "512 B")
        self.assertEqual(fmt_size(1023), "1023 B")
        self.assertEqual(fmt_size(1024), "1.0 KB")
        self.assertEqual(fmt_size(1536), "1.5 KB")
        self.assertEqual(fmt_size(int(12.3 * 1024**2)), "12.3 MB")
        self.assertEqual(fmt_size(int(4.2 * 1024**3)), "4.2 GB")
        self.assertEqual(fmt_size(int(2.5 * 1024**4)), "2.5 TB")

        # Rollover tests: rounded float >= 1024.0 rolls over to next unit
        self.assertEqual(fmt_size(1024**2 - 1), "1.0 MB")
        self.assertEqual(fmt_size(1024**3 - 1), "1.0 GB")
        self.assertEqual(fmt_size(1024**4 - 1), "1.0 TB")

    def test_fmt_date(self):
        self.assertEqual(fmt_date(None), "")
        self.assertEqual(fmt_date(0.0), "")
        self.assertEqual(fmt_date(-100.0), "")

        # Valid epoch timestamp formats as YYYY-MM-DD
        formatted = fmt_date(1793000000.0)
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
