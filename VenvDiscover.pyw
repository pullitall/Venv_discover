"""Venv Discover: double-click to start GUI, or pass arguments for CLI mode."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from venvdiscover.cli import main as cli_main

        sys.exit(cli_main(sys.argv[1:]))

    from venvdiscover.ui.app import main

    main()
