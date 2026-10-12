from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import typing as _ty

from ..path import check_follow
from ..utils import as_error_handler
from ..utils._commit import CommitOnClose
from ._names import _normalize_member_name
from ._snapshot import _Snapshot


class _WritableMembers:
    """What an archive path does to change its archive: gated on
    `self.backend.writable`, which only the zip backend, and only for a local
    archive file, ever reports `True` -- so a tar-backed path, whether reached
    as `TarPath` or auto-detected, correctly raises `NotImplementedError` on
    any write attempt rather than silently misbehaving."""

    __slots__ = ()

    def _write_stream(self, name: str, initial: "bytes | None" = None):
        """A write buffer whose content becomes the member `name` (a key of
        the backend) when it is closed."""
        backend = self.backend
        return CommitOnClose(
            lambda buffer: backend.write_member(name, buffer.getvalue()), initial
        )

    def _member_for_write(self) -> str:
        member = self._member
        if member is None:
            raise ValueError(
                f"member name escapes the archive root: {self.as_posix()!r}"
            )
        return member

    def _check_no_link(self, snapshot: _Snapshot, member: str, *, last: bool) -> None:
        """Writes go to the member named, never through a link: the member
        would land beside the link and no listing would show it."""
        if not snapshot.links:
            return
        parts = member.split("/")
        for count in range(1, len(parts) + (1 if last else 0)):
            if "/".join(parts[:count]) in snapshot.links:
                raise NotImplementedError(
                    f"{self._SCHEME}: writing through a link member is not "
                    f"supported: {self}"
                )

    def _snapshot_or_empty(self) -> _Snapshot:
        """The snapshot a write checks the member against; an archive that is
        not there yet holds nothing."""
        try:
            return self._snapshot()
        except FileNotFoundError:
            return _Snapshot(type(self.backend), [])

    @staticmethod
    def _emptied_parent(
        member: str, index: "dict[str, str]", removed, added: "str | None" = None
    ) -> "str | None":
        """The directory marker (`"a/b/"`) to write when taking `member` out
        of its directory would leave that directory with nothing in it. A zip
        directory may exist only through its members, and `removed` (the
        normalized names leaving) plus `added` (a name arriving) decide
        whether any remain; removing a file never removes its parent."""
        parent = member.rstrip("/").rpartition("/")[0]
        if not parent:
            return None
        marker = f"{parent}/"
        if added is not None and added.startswith(marker):
            return None
        for name in index:
            if name.startswith(marker) and name not in removed:
                return None
        return marker

    def _check_parent(self):
        """pathlib's check before creating `self`: the parent must exist and
        be a directory (a zip directory may be implicit, see `stat`)."""
        parent = self.parent
        if not parent.as_posix().strip("/"):
            return
        try:
            is_dir = parent.stat().is_dir()
        except FileNotFoundError:
            raise FileNotFoundError(
                _errno.ENOENT, "No such file or directory", str(self)
            ) from None
        if not is_dir:
            raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))

    def _require_writable(self):
        backend = self.backend
        if getattr(backend, "writable", False):
            return
        if hasattr(backend, "write_member"):
            # A write-capable backend type (currently only the zip one),
            # just not writable for *this* archive file (a non-local one).
            raise NotImplementedError(
                f"{self._SCHEME}: write support requires a local archive file"
            )
        raise NotImplementedError(
            f"{self._SCHEME}: write support is only available for "
            "zip-format archives"
        )

    def batch(self) -> "_ty.ContextManager[None]":
        """A context manager that holds every change made to this archive
        while it is open and writes them in one replacement of the archive
        file when the outermost block ends without an exception; an exception
        leaves the archive as it was. Inside it every path of the archive
        reads the held changes, and the file is not touched until the block
        ends. A block inside another joins it. `NotImplementedError` when the
        archive cannot be written (see `_require_writable`)."""
        self._require_writable()
        return self.backend.batch()

    def _write_batch(self) -> "_ty.ContextManager[object]":
        # Entered by `copy(recursive=True)` and `PathSyncer` around a tree of
        # writes. It must read nothing and fail never: the write that follows
        # reports a missing or unwritable archive itself.
        try:
            backend = self.backend
            writable = getattr(backend, "writable", False)
        except Exception:
            writable = False
        if not (writable and hasattr(backend, "batch")):
            return _contextlib.nullcontext()
        return backend.batch()

    def _open_for_write(self, mode: str):
        self._require_writable()
        self._check_no_link(
            self._snapshot_or_empty(), self._member_for_write(), last=True
        )
        if "r" in mode:
            # Read-modify-write: what is written reaches the archive on close.
            data = self._read_member().read()
            return self._write_stream(
                self._raw_name(self._member_for_write()), initial=data
            )
        if mode not in ("w", "x"):
            raise NotImplementedError(f"open(mode={mode!r})")
        if self._is_dir_member():
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        if mode == "x" and self.exists():
            raise FileExistsError(str(self))
        self._check_parent()
        # The raw name: overwriting a member the archive spells "./f.txt" rewrites THAT
        # entry instead of appending a second one that shadows it.
        return self._write_stream(self._raw_name(self._member_for_write()))

    def _mkdir(self, mode):
        self._require_writable()
        if not self._member_for_write():
            # The archive root is the archive itself: it exists, or there is
            # nothing to create it in.
            self.stat()
            raise FileExistsError(str(self))
        snapshot = self._snapshot_or_empty()
        if self.exists() or self._member_for_write() in snapshot.links:
            raise FileExistsError(str(self))
        self._check_no_link(snapshot, self._member_for_write(), last=False)
        self._check_parent()
        marker = f"{self._member_for_write()}/"
        self.backend.write_member(self._raw_name(marker), b"")

    def unlink(self, missing_ok=False):
        self._require_writable()
        path = self._member_for_write()
        snapshot = self._snapshot()
        self._check_no_link(snapshot, path, last=False)
        index = snapshot.index
        if path not in index or snapshot.hidden(path):
            if self._is_dir_member():
                raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
            if missing_ok:
                return
            raise FileNotFoundError(str(self))
        self.backend.delete_members(
            snapshot.raw_names(path),
            keep_dir=self._emptied_parent(path, index, {path}),
        )

    def rmdir(self):
        self._require_writable()
        path = self._member_for_write()
        snapshot = self._snapshot()
        self._check_no_link(snapshot, path, last=False)
        index = snapshot.index
        if not path:
            # The archive root cannot be removed: an empty one is a no-op.
            if index:
                raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(self))
            return
        marker = f"{path}/"
        if snapshot.hidden(path):
            raise FileNotFoundError(str(self))
        if any(n != marker and n.startswith(marker) for n in index):
            raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(self))
        if marker not in index:
            if path in index:
                raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
            raise FileNotFoundError(str(self))
        self.backend.delete_members(
            snapshot.raw_names(marker),
            keep_dir=self._emptied_parent(marker, index, {marker}),
        )

    def rm(
        self,
        /,
        recursive=False,
        missing_ok=False,
        ignore_error: "bool | _ty.Callable[[Exception, _ty.Self], bool]" = False,
        *,
        follow_symlinks=False,
        follow_binds=False,
    ):
        # An archive holds no bindings, and a link member is an entry like
        # any other for a removal that does not follow it.
        check_follow("follow_symlinks", follow_symlinks)
        check_follow("follow_binds", follow_binds)
        if not (
            recursive
            and self._rm_tree(
                as_error_handler(ignore_error), follows=follow_symlinks is not False
            )
        ):
            return super().rm(
                recursive=recursive,
                missing_ok=missing_ok,
                ignore_error=ignore_error,
                follow_symlinks=follow_symlinks,
                follow_binds=follow_binds,
            )

    def _rm_tree(self, on_error, follows: bool = False) -> bool:
        """`rm(recursive=True)` of a directory of a writable archive: every
        member under it leaves in ONE rewrite of the archive. False when this
        is not that case, and the generic removal handles (and reports) it:
        a policy that follows links, or leaves them, has to meet each one.
        `on_error(error, self)` is asked about a failure and returns whether to
        carry on."""
        member = self._member
        if member is None or not getattr(self.backend, "writable", False):
            return False
        try:
            snapshot = self._snapshot()
            found = snapshot.stat(member)
            if found is None or not found.is_dir():
                return False
            index = snapshot.index
        except Exception:
            return False
        marker = f"{member}/" if member else ""
        under = [name for name in index if name.startswith(marker)]
        if follows and any(name in snapshot.links for name in under):
            return False
        try:
            # A member under a file is not listed, so a removal that walked
            # the listing would leave it behind and the directory would
            # come back.
            hidden = [name for name in under if snapshot.hidden(name)]
            if hidden:
                raise OSError(
                    _errno.ENOTEMPTY,
                    f"Directory not empty: {hidden[0]!r} lies under a file",
                    str(self),
                )
            if under:
                self.backend.delete_members(
                    [raw for name in under for raw in snapshot.raw_names(name)],
                    keep_dir=(
                        self._emptied_parent(member, index, set(under))
                        if member
                        else None
                    ),
                )
        except Exception as error:
            if not on_error(error, self):
                raise
        return True

    def rename(self, target):
        # A plain str target is a sibling rename (relative to self's parent), as in sftp
        # and ftp. An existing target is replaced as POSIX rename(2) does (a file over a
        # file, a directory over an empty one), never a duplicate member. Returns the new path.
        self._require_writable()
        if isinstance(target, str):
            target = self.parent / target
        elif not isinstance(target, type(self)) or not self._same_filesystem(target):
            raise NotImplementedError("rename() onto another archive")
        old_path = self._member_for_write()
        new_path = _normalize_member_name(target.as_posix().lstrip("/"))
        if not new_path:
            raise ValueError(
                f"member name escapes the archive root: {target.as_posix()!r}"
            )
        snapshot = self._snapshot()
        self._check_no_link(snapshot, old_path, last=False)
        self._check_no_link(snapshot, new_path, last=False)
        index = snapshot.index
        names = list(index)
        marker = f"{old_path}/"
        new_marker = f"{new_path}/"
        renamed = self._rebuild(new_path)
        backend = self.backend
        hidden = snapshot.hidden(old_path)
        is_file = old_path in index and not hidden
        is_dir = not is_file and not hidden and any(n.startswith(marker) for n in names)
        if not (is_file or is_dir):
            raise FileNotFoundError(str(self))
        if is_dir and new_path.startswith(marker):
            raise OSError(
                _errno.EINVAL, "Cannot move a directory into itself", str(target)
            )
        renamed._check_parent()
        if is_file:
            if new_path == old_path:
                return renamed
            if any(n.startswith(new_marker) for n in names):
                raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(target))
            # Every spelling of the source moves, and every spelling of an
            # existing target goes: one left behind would shadow, or come
            # back as, the member this call just placed.
            backend.rename_member(
                index[old_path],
                new_path,
                members={raw: new_path for raw in snapshot.raw_names(old_path)},
                replace=snapshot.raw_names(new_path),
                keep_dir=self._emptied_parent(old_path, index, {old_path}, new_path),
            )
        else:
            if new_marker == marker:
                return renamed
            if new_path in index:
                raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(target))
            if any(n != new_marker and n.startswith(new_marker) for n in names):
                raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(target))
            # The backend renames by its own keys, and a prefix match on the normalized marker
            # misses members spelled "./dir/x" (renaming nothing, or splitting the directory):
            # hand it the exact raw-name mapping.
            moving = [name for name in names if name.startswith(marker)]
            members = {
                raw: new_marker + name[len(marker) :]
                for name in moving
                for raw in snapshot.raw_names(name)
            }
            backend.rename_member(
                index.get(marker, marker),
                new_marker,
                members=members,
                replace=snapshot.raw_names(new_marker),
                keep_dir=self._emptied_parent(old_path, index, set(moving), new_path),
            )
        return renamed
