from __future__ import annotations

import stat as _stat
import struct as _struct
import tempfile as _tempfile
import time as _time
import zipfile as _zipfile

from ._backend import MEMBER_SPOOL_BYTES
from ._snapshot import NO_LINK as _NO_LINK

_ZIP64_EXTRA_ID = 0x0001


def _strip_zip64_extra(extra: bytes) -> bytes:
    """Drop zip64 extended-information records from a raw extra field.
    `zipfile` writes its own when an entry needs them; a copied one would
    carry the old sizes/offsets and conflict with the rewritten entry."""
    kept = bytearray()
    offset = 0
    while offset + 4 <= len(extra):
        header_id, size = _struct.unpack("<HH", extra[offset : offset + 4])
        end = offset + 4 + size
        if header_id != _ZIP64_EXTRA_ID:
            kept += extra[offset:end]
        offset = end
    return bytes(kept)


def _copy_zipinfo(info: _zipfile.ZipInfo, name: str) -> _zipfile.ZipInfo:
    """A fresh `ZipInfo` for `name` carrying `info`'s member metadata
    (timestamp, compression, permissions, comment, extra) -- the fields
    `writestr()` would otherwise reset. Sizes, CRC and offsets are left for
    `writestr()` to compute."""
    copied = _zipfile.ZipInfo(name, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.extra = _strip_zip64_extra(info.extra)
    copied.create_system = info.create_system
    copied.internal_attr = info.internal_attr
    copied.external_attr = info.external_attr
    return copied


def _member_mode(info: _zipfile.ZipInfo) -> "int | None":
    """The Unix mode stored for `info`, or None when there is none: only a
    Unix-made entry (`create_system == 3`) carries one, in the high 16 bits
    of `external_attr`. A special type other than a symlink (which
    `_ZipBackend._stat_of` reports as one) is not modelled and reports no
    mode either."""
    mode = info.external_attr >> 16 if info.create_system == 3 else 0
    if not _stat.S_IMODE(mode):
        return None
    kind = _stat.S_IFMT(mode)
    if info.filename.endswith("/"):
        kind = _stat.S_IFDIR
    elif kind == 0:
        kind = _stat.S_IFREG
    if kind not in (_stat.S_IFREG, _stat.S_IFDIR):
        return None
    return kind | _stat.S_IMODE(mode)


_MAX_LINK_TEXT = 4096
"""The longest text a zip link member may store (`PATH_MAX`); a longer one is
a link whose text cannot be read."""


def _is_link(info: _zipfile.ZipInfo) -> bool:
    """Whether `info` is a symbolic link: an entry made on Unix whose mode
    says so. Its data is the text of the link."""
    return (
        info.create_system == 3
        and not info.filename.endswith("/")
        and _stat.S_ISLNK(info.external_attr >> 16)
    )


_COPY_CHUNK = 1024 * 1024
_LOCAL_HEADER = _struct.Struct("<4s2B4HL2L2H")


def _copy_range(source, target, length: int) -> None:
    """Copy `length` bytes from `source` to `target` in chunks."""
    while length > 0:
        chunk = source.read(min(length, _COPY_CHUNK))
        if not chunk:
            raise _zipfile.BadZipFile("archive ended inside an entry")
        target.write(chunk)
        length -= len(chunk)


def _copy_entry(original, dst, info, new_name: str, end: "int | None") -> bool:
    """Append the entry `info` to the archive being written by `dst` under
    `new_name`, copying its compressed bytes as they are: the local header, the
    data and any data descriptor lie between `info.header_offset` and `end`
    (where the next record starts). Only a renamed entry, or one whose name
    the central directory would write in another encoding than the local
    header, gets a new local header. False, with nothing written, when the
    record is not laid out as its directory says; the caller then recompresses
    it."""
    start = info.header_offset
    if end is None or start < 0 or end <= start:
        return False
    original.seek(start)
    header = original.read(_LOCAL_HEADER.size)
    if len(header) < _LOCAL_HEADER.size:
        return False
    fields = _LOCAL_HEADER.unpack(header)
    header_size = _LOCAL_HEADER.size + fields[10] + fields[11]
    if fields[0] != b"PK\x03\x04" or start + header_size + info.compress_size > end:
        return False
    verbatim = new_name == info.filename and (
        info.filename.isascii() or info.flag_bits & 0x800
    )
    if not verbatim and (
        info.flag_bits & 0x08
        and max(info.file_size, info.compress_size) > _zipfile.ZIP64_LIMIT
    ):
        return False
    copied = _copy_zipinfo(info, new_name)
    for name in (
        "create_version",
        "extract_version",
        "reserved",
        "flag_bits",
        "volume",
        "CRC",
        "compress_size",
        "file_size",
    ):
        setattr(copied, name, getattr(info, name))
    out = dst.fp
    out.seek(dst.start_dir)
    copied.header_offset = out.tell()
    if verbatim:
        original.seek(start)
        _copy_range(original, out, end - start)
    else:
        out.write(copied.FileHeader())
        original.seek(start + header_size)
        _copy_range(original, out, end - start - header_size)
    dst.start_dir = out.tell()
    dst.filelist.append(copied)
    dst.NameToInfo[copied.filename] = copied
    return True


def _remapper(exclude, rename):
    """`remap(name) -> (new name | None, renamed)` for a rewrite: a name in
    `exclude` goes, one in `rename` takes its new name, and a `<name>/` key of
    `rename` also renames every entry nested under that prefix."""
    prefixes = {old: new for old, new in rename.items() if old.endswith("/")}

    def remap(name: str) -> "tuple[str | None, bool]":
        if name in exclude:
            return None, False
        if name in rename:
            return rename[name], True
        for old_prefix, new_prefix in prefixes.items():
            if name.startswith(old_prefix):
                return new_prefix + name[len(old_prefix) :], True
        return name, False

    return remap


def _collapse(items, remap):
    """`[(new name, payload, renamed)]` for the `(name, payload)` pairs of an
    archive after `remap`, in archive order. A renamed entry replaces any
    entry at its new name (POSIX rename); otherwise the last of several
    entries with one name is the one `zipfile` reads, and the others go."""
    plan = []
    winner = {}
    for name, payload in items:
        new_name, renamed = remap(name)
        if new_name is None:
            continue
        previous = winner.get(new_name)
        if previous is None or renamed or not plan[previous][2]:
            winner[new_name] = len(plan)
        plan.append((new_name, payload, renamed))
    return [step for index, step in enumerate(plan) if winner[step[0]] == index]


def _now() -> "tuple[int, ...]":
    """The current local time as a zip stores it: whole even seconds."""
    now = _time.localtime()[:6]
    return (*now[:5], now[5] & ~1)


def _new_info(name: str, size: int) -> _zipfile.ZipInfo:
    """The `ZipInfo` `ZipFile.writestr(name, data)` would give a new entry of
    `size` bytes."""
    info = _zipfile.ZipInfo(name, date_time=_now())
    info.compress_type = _zipfile.ZIP_DEFLATED
    if info.filename.endswith("/"):
        info.external_attr = (0o40775 << 16) | 0x10
    else:
        info.external_attr = 0o600 << 16
    info.file_size = size
    return info


def _renamed_info(info: _zipfile.ZipInfo, name: str) -> _zipfile.ZipInfo:
    """`_copy_zipinfo()` that also keeps the size a listing reports."""
    copied = _copy_zipinfo(info, name)
    copied.file_size = info.file_size
    return copied


class _Scratch:
    """The data a batch holds for members written in it: in memory up to
    `MEMBER_SPOOL_BYTES` altogether, in an unnamed temporary file past that."""

    __slots__ = ("_file", "_end")

    def __init__(self):
        self._file = _tempfile.SpooledTemporaryFile(max_size=MEMBER_SPOOL_BYTES)
        self._end = 0

    def add(self, data: bytes) -> "tuple[int, int]":
        self._file.seek(self._end)
        self._file.write(data)
        reference = (self._end, len(data))
        self._end += len(data)
        return reference

    def get(self, reference: "tuple[int, int]") -> bytes:
        offset, length = reference
        self._file.seek(offset)
        return self._file.read(length)

    def close(self) -> None:
        self._file.close()


class _Pending:
    """One member of a batch: the `ZipInfo` a listing reports for it, the
    entry of the archive file whose stored bytes it is (`origin`, None for a
    member written in the batch) and where the written bytes are (`data`).
    `link` is the text of a link member (None when unreadable), else
    `_NO_LINK`. `seq` is its place in the archive: a renamed member keeps it,
    a new one comes last."""

    __slots__ = ("info", "origin", "data", "link", "seq")

    def __init__(self, info, origin, data, link=_NO_LINK, seq=0):
        self.info = info
        self.origin = origin
        self.data = data
        self.link = link
        self.seq = seq


class _Batch:
    """What a batch changed. `table` maps each member's name to its `_Pending`
    and is read from the archive on the first change; `signature` is the file
    it was read from (None: there was none), checked again before the archive
    is replaced. `snapshot` is the member list derived from the table, kept up
    to date change by change once it exists; `next_seq` is the place of the
    next new member."""

    __slots__ = ("table", "signature", "scratch", "dirty", "snapshot", "next_seq")

    def __init__(self):
        self.table = None
        self.signature = None
        self.scratch = _Scratch()
        self.dirty = False
        self.snapshot = None
        self.next_seq = 0
