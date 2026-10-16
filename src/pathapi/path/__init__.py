"""Object-oriented filesystem paths.

This module provides classes to represent abstract paths and concrete
paths with operations that have semantics appropriate for different
operating systems.
"""

from __future__ import annotations

import importlib as _importlib
import typing as _ty

from ._pure import FsPathLike, P, PN, Pathname, PurePathLike
from ._path import Path, PathLike

if _ty.TYPE_CHECKING:
    from ._async import AsyncFile, AsyncPath

__all__ = [
    "AsyncFile",
    "AsyncPath",
    "FsPathLike",
    "Path",
    "PathLike",
    "Pathname",
    "PurePathLike",
]


def __getattr__(name: str):
    # `asyncio` loads `ssl`, which `import pathapi` must not.
    if name in ("AsyncFile", "AsyncPath"):
        value = getattr(_importlib.import_module("._async", __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
