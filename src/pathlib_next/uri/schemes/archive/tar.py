from __future__ import annotations

from ....archive import TarPath
from ._base import ArchiveUri


class TarUri(ArchiveUri):
    """`tar:` scheme (also handles `.tar.gz`/`.tar.bz2`/`.tar.xz` via
    the standard library's auto-detected "r:*" mode). Read-only. Members stored with a
    `./` prefix are addressed without it. `archive+tar:` is the same scheme
    under a second name. The members are read by a
    `pathlib_next.archive.TarPath`."""

    __SCHEMES = ("tar", "archive+tar")
    __slots__ = ()
    _core_cls = TarPath
