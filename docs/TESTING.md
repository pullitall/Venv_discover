# Testing

Always **run tests with `python scripts/run_tests.py`**, not bare unittest.

Running tests via `python scripts/run_tests.py` runs them on a private, hidden Windows desktop (`WinSta0\<unique-desktop-name>`) so that test windows never appear on your screen, cannot steal focus, and cannot flash. It runs the suite with the Python that started it, or with each `--python` you pass (concurrently), guards against window leaks onto your desktop, and monitors clipboard isolation.

The off-screen hooks in `tests/tk_support.py` remain in place as a second layer of defense for anyone running unittest directly.
