"""`python -m pathlib_next.http.testing [pytest options]` runs the transport suite."""

from __future__ import annotations

import sys

from . import run

if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
