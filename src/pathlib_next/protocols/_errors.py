"""The error shape the operations of the protocols raise."""

from __future__ import annotations

import os as _os


def _os_error(exc_type: type, code: int, path: object) -> OSError:
    """`exc_type(code, strerror, str(path))` -- an OSError shaped like the
    ones `os` raises. `FileNotFoundError(path)` left `errno` and `filename`
    as None, so `e.errno == errno.ENOENT` never matched."""
    return exc_type(code, _os.strerror(code), str(path))
