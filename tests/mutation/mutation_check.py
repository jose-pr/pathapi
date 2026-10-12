"""pytest plugin for `run.py`: fail the session unless the package under test
is the mutated copy named by `MUTATION_ROOT`, not the checkout."""

from __future__ import annotations

import os


def pytest_sessionstart(session):
    import pathlib_next

    expected = os.path.normcase(os.path.abspath(os.environ["MUTATION_ROOT"]))
    loaded = os.path.normcase(os.path.abspath(pathlib_next.__file__))
    if not loaded.startswith(expected + os.sep):
        raise RuntimeError(f"pathlib_next was loaded from {loaded}, not {expected}")
    print(f"\nmutation copy loaded: {pathlib_next.__file__}")
