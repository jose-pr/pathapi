"""Object-oriented filesystem paths.

This module provides classes to represent abstract paths and concrete
paths with operations that have semantics appropriate for different
operating systems.
"""

from __future__ import annotations

from ._pure import FsPathLike, P, PN, Pathname, PurePathLike
from ._path import Path, PathLike

__all__ = [
    "FsPathLike",
    "Path",
    "PathLike",
    "Pathname",
    "PurePathLike",
]
