import unittest

from venvdiscover.model import Env, Kind, Package
from venvdiscover.query import env_sort_key, filter_packages, search


class QueryTests(unittest.TestCase):
    def test_search(self):
        env1 = Env(
            path=r"C:\proj1\.venv",
            kind=Kind.VENV,
            name="proj1",
            packages=[
                Package("numpy", "2.1.0", "pip"),
                Package("scikit-learn", "1.5.0", "pip"),
            ],
        )
        env2 = Env(
            path=r"C:\miniconda\envs\ds",
            kind=Kind.CONDA,
            name="ds",
            packages=[
                Package("NumPy", "1.26.4", "conda"),
                Package("pandas", "2.2.0", "conda"),
            ],
        )
        env3 = Env(
            path=r"C:\proj2\.venv",
            kind=Kind.VENV,
            name="proj2",
            packages=[
                Package("requests", "2.31.0", "pip"),
            ],
        )
        envs = [env1, env2, env3]

        # "Num" finds numpy in two envs, case-insensitive, sorted by (pkg name, env name)
        results = search(envs, "Num")
        self.assertEqual(len(results), 2)
        envs_found = [r[0].name for r in results]
        pkgs_found = [r[1].name.lower() for r in results]
        self.assertIn("proj1", envs_found)
        self.assertIn("ds", envs_found)
        self.assertEqual(pkgs_found, ["numpy", "numpy"])

        # "scikit_learn" matches scikit-learn
        results_sk = search(envs, "scikit_learn")
        self.assertEqual(len(results_sk), 1)
        self.assertEqual(results_sk[0][0].name, "proj1")
        self.assertEqual(results_sk[0][1].name, "scikit-learn")

        # "" and whitespace return []
        self.assertEqual(search(envs, ""), [])
        self.assertEqual(search(envs, "   "), [])

    def test_filter_packages(self):
        pkgs = [
            Package("numpy", "2.1.0", "pip"),
            Package("scikit-learn", "1.5.0", "pip"),
            Package("requests", "2.31.0", "pip"),
        ]

        filtered = filter_packages(pkgs, "scikit_learn")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].name, "scikit-learn")

        filtered_req = filter_packages(pkgs, "req")
        self.assertEqual(len(filtered_req), 1)
        self.assertEqual(filtered_req[0].name, "requests")

        # Empty query returns full list
        self.assertEqual(filter_packages(pkgs, ""), pkgs)
        self.assertEqual(filter_packages(pkgs, "   "), pkgs)

    def test_env_sort_key_python_version_order(self):
        # Numerical sorting of python versions: 3.9 < 3.10 < 3.13, empty string first
        e_empty = Env(r"C:\e0", Kind.VENV, "e0", python_version="")
        e_39 = Env(r"C:\e1", Kind.VENV, "e1", python_version="3.9.18")
        e_310 = Env(r"C:\e2", Kind.VENV, "e2", python_version="3.10.11")
        e_313 = Env(r"C:\e3", Kind.VENV, "e3", python_version="3.13.5")

        sorted_envs = sorted(
            [e_313, e_39, e_empty, e_310],
            key=lambda e: env_sort_key(e, "python"),
        )
        self.assertEqual(sorted_envs, [e_empty, e_39, e_310, e_313])

    def test_env_sort_key_temp_always_last(self):
        # Temp venv given values that would otherwise sort FIRST across all columns:
        # name 'aaa', size 1, last_used 1.0, python 2.7.18, 0 packages, location C:\000
        temp_first_candidate = Env(
            path=r"C:\000",
            kind=Kind.TEMP_VENV,
            name="aaa",
            python_version="2.7.18",
            packages=[],
            size=1,
            last_used=1.0,
        )

        env1 = Env(
            path=r"C:\e1",
            kind=Kind.VENV,
            name="mmm",
            python_version="3.10.0",
            packages=[Package("p1", "1.0", "pip")],
            size=100,
            last_used=100.0,
        )

        env2 = Env(
            path=r"C:\e2",
            kind=Kind.CONDA,
            name="zzz",
            python_version="3.12.0",
            packages=[Package("p1", "1.0", "pip"), Package("p2", "1.0", "pip")],
            size=1000,
            last_used=1000.0,
        )

        env_none_size = Env(
            path=r"C:\e3",
            kind=Kind.VENV,
            name="nnn",
            python_version="3.11.0",
            packages=[Package("p1", "1.0", "pip")],
            size=None,
            last_used=None,
        )

        # In size sorting: None size comes before numbers, and temp is always last
        sorted_size = sorted(
            [temp_first_candidate, env2, env1, env_none_size],
            key=lambda e: env_sort_key(e, "size"),
        )
        self.assertEqual(sorted_size, [env_none_size, env1, env2, temp_first_candidate])

        # Assert temp is strictly last in ALL 7 columns
        for col in ("name", "kind", "python", "packages", "size", "last_used", "location"):
            sorted_col = sorted(
                [temp_first_candidate, env2, env1],
                key=lambda e: env_sort_key(e, col),
            )
            self.assertEqual(
                sorted_col[-1],
                temp_first_candidate,
                f"TEMP_VENV was not last when sorting by column '{col}'",
            )


if __name__ == "__main__":
    unittest.main()
