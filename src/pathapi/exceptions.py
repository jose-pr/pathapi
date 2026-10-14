"""The exceptions `pathapi` defines.

A failure of the file system or of a backend is the `OSError` type that
`pathlib` raises for the same condition (`FileNotFoundError`,
`PermissionError`, ...); this module holds what has no such type. A bad
argument is a plain `ValueError` or `TypeError`.
"""

from __future__ import annotations

__all__ = ["NonRelativePatternError", "PathError"]


class PathError(Exception):
    """Base class of the exceptions `pathapi` defines for a failure at
    run time that no builtin describes."""


class NonRelativePatternError(PathError, NotImplementedError, ValueError):
    """An absolute or anchored glob pattern. pathlib raises
    `NotImplementedError("Non-relative patterns are unsupported")`; this is
    also a `ValueError`, so either `except` clause catches it."""
