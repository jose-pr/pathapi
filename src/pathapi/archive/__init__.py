"""Zip and tar archives as trees of paths, on the standard library alone
(`zipfile`, `tarfile`).

`ArchivePath`, `ZipPath` and `TarPath` are `Path`s for the members of an
archive file, which is any `Path`; they need no URI.
"""

from __future__ import annotations

import sys as _sys
import types as _types

from . import _backend
from ._pack import detect_format, make_archive, unpack_archive
from ._path import ArchivePath, TarPath, ZipPath
from ._snapshot import MAX_LINKS, LinkTarget

__all__ = [
    "ArchivePath",
    "LinkTarget",
    "MAX_LINKS",
    "MEMBER_SPOOL_BYTES",
    "TarPath",
    "ZipPath",
    "detect_format",
    "make_archive",
    "unpack_archive",
]


class _Module(_types.ModuleType):
    """The module, with `MEMBER_SPOOL_BYTES` (the size past which a member read
    goes to a temporary file) read from and written to where it takes effect."""

    @property
    def MEMBER_SPOOL_BYTES(self) -> int:
        return _backend.MEMBER_SPOOL_BYTES

    @MEMBER_SPOOL_BYTES.setter
    def MEMBER_SPOOL_BYTES(self, value: int) -> None:
        _backend.MEMBER_SPOOL_BYTES = value


_sys.modules[__name__].__class__ = _Module
