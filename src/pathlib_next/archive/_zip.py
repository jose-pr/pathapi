from __future__ import annotations

import errno as _errno
import io as _io
import os as _os
import shutil as _shutil
import stat as _stat
import struct as _struct
import tempfile as _tempfile
import time as _time
import zipfile as _zipfile
import zlib as _zlib
from contextlib import contextmanager as _contextmanager

from ..utils.stat import FileStat
from ._backend import _ArchiveBackend, _LazyReadFile, _local_outer_path, _spool
from ._snapshot import NO_LINK as _NO_LINK
from ._snapshot import _Snapshot
from ._zipentries import (
    _MAX_LINK_TEXT,
    _Batch,
    _collapse,
    _copy_entry,
    _copy_range,
    _copy_zipinfo,
    _is_link,
    _member_mode,
    _new_info,
    _now,
    _Pending,
    _remapper,
    _renamed_info,
)


class _ZipBackend(_ArchiveBackend):
    __slots__ = ("_batch",)

    _FORMAT_ERROR = _zipfile.BadZipFile

    def __init__(self, outer):
        super().__init__(outer)
        self._batch = None

    def _is_format_error(self, error):
        # `zipfile` lets the decoders' own errors through for a damaged archive: an inflate
        # failure, a short header, a truncated stream, an undecodable name, an offset that
        # cannot be seeked to, an unknown version (`NotImplementedError` in the directory).
        return isinstance(
            error,
            (_zlib.error, _struct.error, EOFError, UnicodeDecodeError, OverflowError),
        ) or (isinstance(error, OSError) and error.errno == _errno.EINVAL)

    @property
    def writable(self) -> bool:
        # Writing entries needs a real seekable local file -- not a
        # remote/embedded outer URI. The shared read handle is still opened
        # "r"; writes open their own handle (`write_member`/`_rewrite`).
        return _local_outer_path(self.outer) is not None

    def _open(self):
        if self.writable:
            # Never "a" for reads: append mode opens the file r+b (fails on a
            # read-only archive), creates a missing file, and on close appends
            # an end-of-central-directory record to a file that is not a zip.
            fp = _LazyReadFile(self._outer_file())
            try:
                return self._read_directory(fp)
            finally:
                fp.release()
        return self._read_directory(_io.BytesIO(self._outer_bytes()))

    def _read_directory(self, fileobj):
        try:
            with self._damage_as_format_error():
                return _zipfile.ZipFile(fileobj, mode="r")
        except NotImplementedError as error:
            # An extract version the central directory declares and
            # `zipfile` does not know: the directory is not one.
            raise _zipfile.BadZipFile(str(error)) from error

    def _close_handle(self):
        # `ZipFile.close()` leaves a passed-in file object open.
        fp = getattr(self._handle, "fp", None)
        super()._close_handle()
        if isinstance(fp, _LazyReadFile):
            fp.release()

    @_contextmanager
    def _reading(self):
        """The shared handle, under the lock; the OS file handle behind it
        is released again when the operation ends."""
        with self._lock:
            handle = self.handle
            try:
                with self._damage_as_format_error():
                    yield handle
            finally:
                if isinstance(handle.fp, _LazyReadFile):
                    handle.fp.release()

    def _entries(self):
        return [(info.filename, info) for info in self._handle.infolist()]

    @staticmethod
    def _is_directory(info):
        return info.filename.endswith("/")

    def _link_texts(self, entries):
        links = {}
        for raw, info in entries:
            if _is_link(info):
                links[raw] = self._read_link(info)
        return links

    def _read_link(self, info) -> "str | None":
        if info.file_size > _MAX_LINK_TEXT:
            return None
        try:
            with self._reading() as handle:
                with handle.open(info) as source:
                    data = source.read(_MAX_LINK_TEXT + 1)
        except Exception:
            return None
        if len(data) > _MAX_LINK_TEXT:
            return None
        return data.decode("utf-8", "surrogateescape")

    @staticmethod
    def _stat_of(info, snapshot):
        mtime = int(_time.mktime((*info.date_time, 0, 0, -1))) if info.date_time else 0
        if _is_link(info):
            mode = _stat.S_IMODE(info.external_attr >> 16) or 0o777
            return FileStat(
                st_mode=_stat.S_IFLNK | mode, st_size=info.file_size, st_mtime=mtime
            )
        return FileStat(
            st_mode=_member_mode(info),
            st_size=info.file_size,
            st_mtime=mtime,
            is_dir=info.filename.endswith("/"),
        )

    def snapshot(self) -> _Snapshot:
        with self._lock:
            batch = self._batch
            if batch is None or not batch.dirty:
                return super().snapshot()
            if batch.snapshot is None:
                members = self._ordered()
                batch.snapshot = _Snapshot(
                    type(self),
                    [(name, entry.info) for name, entry in members],
                    {
                        name: entry.link
                        for name, entry in members
                        if entry.link is not _NO_LINK
                    },
                )
            return batch.snapshot

    def read_member(self, path):
        with self._lock:
            batch = self._batch
            if batch is not None and batch.dirty:
                entry = self._table()[path]  # raises KeyError if missing
                if entry.data is not None:
                    return _io.BytesIO(batch.scratch.get(entry.data))
                path = entry.origin
            with self._reading() as handle:
                # Copied out, not the live ZipExtFile: callers may hold the stream across later
                # mutations on this shared backend, which close and reopen the handle. A large
                # member goes to a temporary file, not to memory.
                with handle.open(path) as source:  # raises KeyError if missing
                    return _spool(source)

    @_contextmanager
    def batch(self):
        """Holds every change made while it is open, for every path of this
        archive, and writes them in ONE replacement of the archive when the
        outermost block ends cleanly. An exception leaves the archive as it
        was. The block owns the archive: another thread waits for it."""
        with self._lock:
            outermost = self._batch is None
            if outermost:
                self._batch = _Batch()
            try:
                yield
                if outermost:
                    self._flush(self._batch)
            finally:
                if outermost:
                    batch, self._batch = self._batch, None
                    batch.scratch.close()

    def _changed(self) -> OSError:
        return OSError(
            _errno.EBUSY,
            "The archive changed while a batch of changes to it was open",
            self._outer_file(),
        )

    def _table(self) -> "dict[str, _Pending]":
        """The members of the open batch by name, read from the archive on
        first use. Must be called with `self._lock` held."""
        batch = self._batch
        if batch.table is not None:
            if self._outer_signature() != batch.signature:
                raise self._changed()
            return batch.table
        table: "dict[str, _Pending]" = {}
        outer_path = self._outer_file()
        try:
            with self._reading() as handle:
                infos = handle.infolist()
            links = self._link_texts([(info.filename, info) for info in infos])
            for seq, info in enumerate(infos):
                # The last of several entries of one name is the live one.
                table[info.filename] = _Pending(
                    info, info, None, links.get(info.filename, _NO_LINK), seq
                )
            batch.next_seq = len(infos)
            signature = self._signature
        except FileNotFoundError:
            if _os.path.lexists(outer_path):
                raise
            signature = None  # the first change creates the archive
        else:
            self._check_writable(_os.path.realpath(outer_path))
        batch.table, batch.signature = table, signature
        return table

    def _ordered(self) -> "list[tuple[str, _Pending]]":
        """The members of the open batch in the order of the archive."""
        return sorted(self._table().items(), key=lambda item: item[1].seq)

    def _attach(self, name: str, entry: _Pending) -> None:
        """Make `entry` the member `name`. The member list the batch has
        derived takes the change itself; one that cannot (a name the archive
        spells two ways) is derived again on the next read. Must be called with
        `self._lock` held."""
        batch = self._batch
        batch.table[name] = entry
        batch.dirty = True
        if batch.snapshot is not None and not batch.snapshot.add(
            name, entry.info, entry.link
        ):
            batch.snapshot = None

    def _detach(self, name: str) -> "_Pending | None":
        """Remove the member `name` and return it; see `_attach()`."""
        batch = self._batch
        entry = batch.table.pop(name, None)
        if entry is not None:
            batch.dirty = True
            if batch.snapshot is not None and not batch.snapshot.drop(name):
                batch.snapshot = None
        return entry

    def _put(self, path: str, data: bytes) -> None:
        """Set member `path` to `data`, keeping an existing member's place and
        metadata. Must be called with `self._lock` held."""
        batch = self._batch
        table = self._table()
        info = _new_info(path, len(data))
        old = table.get(info.filename)
        if old is not None:
            info = _copy_zipinfo(old.info, info.filename)
            info.date_time = _now()
            info.file_size = len(data)
            seq = old.seq
        else:
            seq = batch.next_seq
            batch.next_seq += 1
        self._attach(
            info.filename,
            _Pending(info, None, batch.scratch.add(data), _NO_LINK, seq),
        )

    def _flush(self, batch: _Batch) -> None:
        if not batch.dirty:
            return
        table = batch.table
        outer_path = _os.path.realpath(self._outer_file())

        members = sorted(table.items(), key=lambda item: item[1].seq)

        def steps():
            for name, entry in members:
                if entry.data is not None:
                    yield name, None, entry.info, batch.scratch.get(entry.data)
                else:
                    yield name, entry.origin, None, None

        if batch.signature is None:
            created = False
            try:
                with open(outer_path, "xb") as fp:
                    created = True
                    with _zipfile.ZipFile(fp, "w", _zipfile.ZIP_DEFLATED) as dst:
                        for _name, _origin, zinfo, data in steps():
                            dst.writestr(zinfo, data)
            except BaseException:
                if created:
                    try:
                        _os.unlink(outer_path)
                    except OSError:
                        pass
                raise
            return
        if self._outer_signature() != batch.signature:
            raise self._changed()

        def fill(tmp):
            with open(outer_path, "rb") as original:
                with _zipfile.ZipFile(original, "r") as src:
                    self._emit(tmp, original, src, steps())

        self._replace_outer(fill)

    def write_member(self, path: str, data: bytes):
        with self._lock:
            if self._batch is not None:
                self._put(path, data)
                return
            outer_path = self._outer_file()
            if not _os.path.lexists(outer_path):
                # First write into a missing archive creates it ("x": never
                # clobber a file that appeared in the meantime).
                self._close_handle()
                with _zipfile.ZipFile(
                    outer_path, "x", compression=_zipfile.ZIP_DEFLATED
                ) as archive:
                    archive.writestr(path, data)
                return
            if path in self.snapshot().infos:
                # zipfile has no in-place entry update -- writestr()-ing an
                # existing name just appends a duplicate. Overwriting an
                # existing entry needs a full-archive rewrite.
                self._rewrite(overwrite={path: data})
                return
            self._append(path, data)

    def _append(self, path: str, data: bytes):
        """Add a new entry without risking the archive: an in-place "a"
        append overwrites the central directory with the new entry's data
        and writes a new one only on close, so dying in between left no
        readable member. The archive is byte-copied (nothing recompressed)
        to a temp file, appended to there, and swapped in by
        `_replace_outer`."""
        outer_path = _os.path.realpath(self._outer_file())
        if not _zipfile.is_zipfile(outer_path):
            raise _zipfile.BadZipFile(f"File is not a zip file: {outer_path!r}")

        def fill(tmp):
            with open(outer_path, "rb") as original:
                _shutil.copyfileobj(original, tmp)
            # zipfile only persists the central directory to disk when the
            # *archive* (not just the entry) is closed.
            with _zipfile.ZipFile(
                tmp, "a", compression=_zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr(path, data)

        self._replace_outer(fill)

    @staticmethod
    def _check_writable(outer_path: str) -> None:
        if not _os.access(outer_path, _os.W_OK):
            # Replacing swaps the file for a new one and so ignores its mode:
            # a read-only archive is refused here, as an `open(..., "ab")`
            # would refuse it.
            raise PermissionError(
                _errno.EACCES, _os.strerror(_errno.EACCES), outer_path
            )

    def _replace_outer(self, fill):
        """Build the new archive with `fill(tmp)` in a temp file next to the
        archive -- resolving a symlink, so the link's target is what gets
        replaced -- fsync it, keep the original's file mode, and atomically
        replace the original (`os.replace`): a failure or crash at any point
        leaves the original untouched and no temp file behind. Must be
        called with `self._lock` held."""
        outer_path = _os.path.realpath(self._outer_file())
        self._check_writable(outer_path)
        self._close_handle()
        fd, tmp_name = _tempfile.mkstemp(
            dir=_os.path.dirname(outer_path), prefix=".pathlib_next-zip-", suffix=".tmp"
        )
        try:
            with _os.fdopen(fd, "w+b") as tmp:
                fill(tmp)
                tmp.flush()
                _os.fsync(tmp.fileno())
            _shutil.copymode(outer_path, tmp_name)
            _os.replace(tmp_name, outer_path)
        except BaseException:
            try:
                _os.chmod(tmp_name, _stat.S_IREAD | _stat.S_IWRITE)
                _os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def delete_members(self, names, *, keep_dir: "str | None" = None):
        """Drop every entry named in `names` in one rewrite. `keep_dir`
        (`"a/b/"`) is written in the same rewrite as a directory entry: a
        directory that exists only through its members must outlive them."""
        with self._lock:
            if self._batch is not None:
                self._table()
                for name in names:
                    self._detach(name)
                self._batch.dirty = True
                if keep_dir:
                    self._put(keep_dir, b"")
                return
            self._rewrite(
                exclude=set(names),
                overwrite={keep_dir: b""} if keep_dir else None,
            )

    def rename_member(
        self,
        old: str,
        new: str,
        *,
        members=None,
        replace=(),
        keep_dir: "str | None" = None,
    ):
        """Rename `old` to `new`. `members` is an explicit raw-name mapping
        for a directory rename, whose members may be spelled several ways
        ("./dir/x") and so cannot be found by prefix on one name. `replace`
        names entries the rename overwrites, and `keep_dir` is a directory
        entry written in the same rewrite (see `delete_members`)."""
        with self._lock:
            rename = dict(members) if members else {old: new}
            if self._batch is not None:
                self._rename_pending(set(replace), rename)
                if keep_dir:
                    self._put(keep_dir, b"")
                return
            self._rewrite(
                exclude=set(replace),
                rename=rename,
                overwrite={keep_dir: b""} if keep_dir else None,
            )

    def _rename_pending(self, exclude, rename) -> None:
        """`_rewrite()`'s exclusions and renames, applied to the open batch:
        a renamed member keeps its place and replaces whatever has its new
        name, and a `<name>/` key of `rename` also moves every member below
        it. Costs the members moved, and, for a `<name>/` key, one pass over
        the members."""
        table = self._table()
        for name in exclude:
            self._detach(name)
        moving = {
            old: new for old, new in rename.items() if old in table and old != new
        }
        prefixes = {old: new for old, new in rename.items() if old.endswith("/")}
        if prefixes:
            for name in table:
                if name in rename:
                    continue
                for prefix, new_prefix in prefixes.items():
                    if name.startswith(prefix):
                        moving[name] = new_prefix + name[len(prefix) :]
                        break
        entries = sorted((table[name].seq, new, name) for name, new in moving.items())
        taken = [(new, self._detach(name)) for _seq, new, name in entries]
        for new, entry in taken:
            self._detach(new)
            self._attach(
                new,
                _Pending(
                    _renamed_info(entry.info, new),
                    entry.origin,
                    entry.data,
                    entry.link,
                    entry.seq,
                ),
            )
        self._batch.dirty = True

    def _rewrite(
        self,
        *,
        exclude: "set[str]" = frozenset(),
        rename: "dict[str, str] | None" = None,
        overwrite: "dict[str, bytes] | None" = None,
    ):
        """Safely rewrite the whole archive: drop names in `exclude`,
        rename per `rename` (old->new; a `<name>/` key also renames every
        entry nested under that prefix), and set/add content for names in
        `overwrite`. A renamed entry replaces any existing entry at its new
        name (POSIX rename); otherwise duplicate names collapse to the last
        one, which is the entry `zipfile` itself reads.

        An entry that is only kept or renamed is copied as it lies in the
        archive (`_copy_entry`): nothing is decompressed, so the cost is the
        stored size, and an encrypted entry or one with a compression method
        `zipfile` lacks survives. Every kept entry keeps its metadata, and the
        archive keeps its comment, any bytes before the first member (a
        zipapp shebang, a self-extractor stub) and its file mode. Written
        through `_replace_outer`, so a crash mid-rewrite can't leave a
        corrupt archive. Must be called with `self._lock` held (all public
        mutators above already do)."""
        rename = dict(rename or {})
        overwrite = dict(overwrite or {})
        remap = _remapper(exclude, rename)
        outer_path = _os.path.realpath(self._outer_file())

        def steps(src):
            written = set()
            for new_name, info, _renamed in _collapse(
                ((info.filename, info) for info in src.infolist()), remap
            ):
                data = overwrite.pop(new_name, None)
                if data is None:
                    data = overwrite.pop(info.filename, None)
                if data is not None:
                    zinfo = _copy_zipinfo(info, new_name)
                    zinfo.date_time = _time.localtime()[:6]
                    yield new_name, None, zinfo, data
                else:
                    yield new_name, info, None, None
                written.add(new_name)
            for name, data in overwrite.items():
                if name not in written:
                    yield name, None, None, data

        def fill(tmp):
            with open(outer_path, "rb") as original:
                with _zipfile.ZipFile(original, "r") as src:
                    self._emit(tmp, original, src, steps(src))

        self._replace_outer(fill)

    @staticmethod
    def _emit(tmp, original, src, steps):
        """Write the entries `steps` yields as `(name, origin, zinfo, data)`
        to `tmp`, after the bytes the archive `src` holds before its first
        member: with `data`, a new entry (`zinfo`, else a default one);
        without, the entry `origin` of `src`'s file, copied as it lies."""
        infos = src.infolist()
        # Where each entry's record ends: where the next one starts, or the
        # central directory.
        starts = sorted({info.header_offset for info in infos} | {src.start_dir})
        ends = dict(zip(starts, starts[1:]))
        original.seek(0)
        _copy_range(original, tmp, starts[0])

        with _zipfile.ZipFile(tmp, "w", _zipfile.ZIP_DEFLATED) as dst:
            dst.comment = src.comment
            for name, origin, zinfo, data in steps:
                if data is not None:
                    dst.writestr(name if zinfo is None else zinfo, data)
                elif not _copy_entry(
                    original, dst, origin, name, ends.get(origin.header_offset)
                ):
                    dst.writestr(_copy_zipinfo(origin, name), src.read(origin))
