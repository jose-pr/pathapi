"""The walk down a tree that `rm(recursive=True)` makes."""

from __future__ import annotations

import typing as _ty

from .. import _names
from .._filestat import FileStat
from ..glob import _entries
from ._removal import _follow_policy, _InvalidPolicy, _listing

if _ty.TYPE_CHECKING:
    from ..path import Path


class _Removal:
    """One `rm()` call: the error handler, the follow policies and the errors
    already declined. It lives for the length of the call."""

    __slots__ = ("onerror", "follow_symlinks", "follow_binds", "declined")

    def __init__(self, onerror, follow_symlinks, follow_binds):
        self.onerror = onerror
        self.follow_symlinks = follow_symlinks
        self.follow_binds = follow_binds
        # An error the handler declined, or raised itself, on its way out:
        # each enclosing directory's `except` catches it again, and consulting
        # the handler there reported one failure once per ancestor.
        self.declined: list = []

    def handle(self, error, path) -> None:
        if isinstance(error, _InvalidPolicy):
            # A mistake of the caller, not a removal that failed.
            raise error
        if any(error is seen for seen in self.declined):
            raise error
        try:
            swallowed = self.onerror(error, path)
        except BaseException as raised:
            self.declined.append(raised)
            raise
        if not swallowed:
            self.declined.append(error)
            raise error

    def tree(self, path: "Path", *, link: bool = False) -> None:
        """Remove what is in the directory `path`, then `path` itself (an
        `unlink()` when it is the symlink to a directory, `link`)."""
        try:
            entries = list(_listing(path))
        except Exception as error:
            self.handle(error, path)
            return

        for name, child_stat in entries:
            self._child(path, name, child_stat)

        try:
            # `rmdir()` on a symlink is ENOTDIR on POSIX: the link is
            # unlinked once what is behind it has been removed.
            path.unlink() if link else path.rmdir()
        except Exception as error:
            self.handle(error, path)

    def _child(self, path: "Path", name: str, child_stat) -> None:
        child = None
        try:
            # Checked before any child is built from the name; only the one-component
            # rule, as a listed name is not rewritten by the platform that listed it.
            if not _names.is_safe_child_name(name):
                raise ValueError(f"refusing unsafe child name {name!r} under {path}")
            child = _entries._child(path, name)
            if child_stat is None:
                child_stat = FileStat.from_path(child, follow_symlink=False)
            if child_stat is not None and child_stat.is_symlink():
                # A link, whatever it points at. The non-following
                # stat says "link", so this branch is reached before
                # the directory one.
                policy = _follow_policy(self.follow_symlinks, child)
                if policy == "ignore":
                    return
                if policy == "follow" and child.is_dir():
                    self.tree(child, link=True)
                else:
                    child.unlink()
            elif child_stat is not None and child_stat.is_dir():
                if child.is_dir_binding():
                    # A binding: what is inside belongs to the tree
                    # it names, not to this one.
                    policy = _follow_policy(self.follow_binds, child)
                    if policy == "ignore":
                        return
                    if policy == "follow":
                        self.tree(child)
                    else:
                        # `rmdir()` on a live mount fails (EBUSY) --
                        # the right answer, and far better than
                        # emptying someone else's filesystem.
                        child.rmdir()
                else:
                    self.tree(child)
            else:
                child.unlink()
        except Exception as error:
            self.handle(error, path if child is None else child)
