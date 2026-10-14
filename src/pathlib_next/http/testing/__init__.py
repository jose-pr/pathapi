"""Testing support: the suite that holds every HTTP transport to the same
promises.

This module is testing support, not part of the stable API: it is importable
and documented, but its contents (the test names, the fixtures, the helper
signatures, `MODULES` and `HELPERS`) may change in any release. `pathlib_next.http`
never imports it.

`HTTPClient` and the transport contract are tested over a transport chosen by
name, against loopback servers scripted at the byte level. A distribution that
provides a transport (see `pathlib_next.http.get_transport`) runs the same
tests over its own, with its distribution installed::

    python -m pathlib_next.http.testing --http-transports requests -q

Everything after `python -m pathlib_next.http.testing` is passed to pytest,
which this module needs. `--http-transports` takes names that
`pathlib_next.http.transport_names()` lists, separated by commas; it defaults to
`builtin`. Every request goes to the loopback interface: the suite fails a test
that tries to reach any other host. The TLS tests need `cryptography` and skip
without it. This module imports nothing but the standard library: importing it
loads `pathlib_next.http` (and so `ssl`) but not `pytest`, which `run()` imports.
"""

from __future__ import annotations

import pathlib as _pathlib
import typing as _ty

__all__ = ["HELPERS", "MODULES", "run", "suite_directory"]

MODULES = (
    "test_client_default.py",
    "test_client_errors.py",
    "test_client_peer_text.py",
    "test_client_redirect_origin.py",
    "test_client_redirects.py",
    "test_client_reply_limits.py",
    "test_client_write_redirects.py",
    "test_transport_body.py",
    "test_transport_send.py",
)
"""The test modules that run once per transport, by file name."""

HELPERS = (
    "conftest.py",
    "transport_fakes.py",
    "transport_guard.py",
    "transport_wire.py",
)
"""The files beside them that they import: the fixtures and the loopback servers."""


def suite_directory() -> _pathlib.Path:
    """The directory that holds `MODULES` and `HELPERS`: the copy installed
    with the package, or `tests/transport/` of a source checkout. Raises
    `FileNotFoundError` when neither is there."""
    here = _pathlib.Path(__file__).resolve().parent
    for candidate in (here / "suite", here.parents[3] / "tests" / "transport"):
        if all((candidate / name).is_file() for name in (*MODULES, *HELPERS)):
            return candidate
    raise FileNotFoundError("the transport suite is not installed with pathlib-next")


def run(arguments: "_ty.Sequence[str] | None" = None) -> int:
    """Runs `MODULES` with pytest and `arguments` (pytest options, such as
    `--http-transports requests`) and returns the exit status. Raises
    `ImportError` when pytest is not installed."""
    try:
        import pytest
    except ImportError as error:
        raise ImportError(
            "the transport suite needs pytest: pip install pytest", name="pytest"
        ) from error
    directory = suite_directory()
    files = [str(directory / name) for name in MODULES]
    # The files go first: with an option and its value ahead of them, pytest
    # 8 reads the value as the first path and loads no `conftest.py`, so the
    # option `--http-transports` is not known.
    return int(pytest.main([*files, *(arguments or ())]))
