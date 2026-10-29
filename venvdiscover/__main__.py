"""Entry point for running Venv Discover CLI via `python -m venvdiscover`."""

from __future__ import annotations

import sys
from venvdiscover.cli import main

if __name__ == "__main__":
    sys.exit(main())
