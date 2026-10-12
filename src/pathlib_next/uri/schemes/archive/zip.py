from __future__ import annotations

from ....archive import ZipPath
from ._base import ArchiveUri


class ZipUri(ArchiveUri):
    """`zip:` scheme (`archive+zip:` is a second name for it). Read/write: write support (new entries, overwriting
    existing entries, `unlink`/`rmdir`/`rename`) works when the outer
    archive is a local `file:` URI; every other outer scheme is read-only
    (fetched fully into memory first). Every mutation replaces the archive
    atomically (temp file + `os.replace`), so the result is a new file: other
    hard links keep the old content, and an archive the caller may not write
    is refused first. A new entry is appended, deflated, to a byte copy of the
    archive (nothing recompressed); overwriting/deleting/renaming an existing
    entry rewrites the whole archive, since the standard library's zip module has no in-place
    entry mutation, keeping every other entry's metadata and compression method.
    The members are read and written by a `pathlib_next.archive.ZipPath`."""

    __SCHEMES = ("zip", "archive+zip")
    __slots__ = ()
    _core_cls = ZipPath
