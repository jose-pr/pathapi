"""Symbolic links."""

from __future__ import annotations

import typing as _ty

from .. import _stubs
from .fs import Stat
from .tree import Remove

__all__ = ["Hardlink", "Readlink", "Symlink"]


class Readlink(Stat, _ty.Protocol):
    """Reading a symbolic link: `readlink()` is the primitive. A read-only
    backend (an archive) has this and not `Symlink`."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("readlink",)

    @_stubs.primitive
    def readlink(self) -> _ty.Self:
        """The path this symbolic link points to, as a path of this class whose
        `str()` and `as_posix()` are the link text, unresolved (a stub:
        `NotImplementedError`). `OSError(EINVAL)` where the path is not a
        link."""
        return _stubs._unimplemented("readlink")


class Symlink(Remove, _ty.Protocol):
    """Making a symbolic link: `_symlink_to()` is the primitive;
    `symlink_to()` normalizes its target and honours `force=`."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (("_symlink_to", "symlink_to"),)

    @_stubs.primitive
    def _symlink_to(
        self, target: "_ty.Self", target_is_directory: bool = False
    ) -> None:
        """Create a symlink at this path pointing at `target`.

        The backend primitive behind `symlink_to()`, in the same shape as
        `_mkdir`/`_open`: implement only this, and the generic conveniences
        (`force=`) come for free. `target` has already been normalized to a
        path object of this class by `symlink_to()` -- accepting `str` and
        turning it into a path is the wrapper's job, not the primitive's.

        Implementations take the raw target string from the path object the
        way their own backend needs it (the path of the request for wire
        protocols, `os.fspath()`/`as_posix()` locally); it never carries a scheme or
        host prefix, and a relative target stays relative.
        """
        return _stubs._unimplemented("_symlink_to")

    def _symlink_target(self, target: "_ty.Self | str") -> "_ty.Self":
        """Normalize a `symlink_to()` target argument to a path object.

        A `str` target is the **literal link target** -- whatever it says
        is what gets stored, verbatim and unresolved, exactly as
        `pathlib.Path.symlink_to()` does. The default builds it with
        `with_segments()`, which keeps per-instance state (a `MemPath`'s
        backend) but normalizes the text as this class parses a path;
        override it wherever that would reinterpret the string instead of
        taking it literally (`LocalPath` hands it on untouched, and a class
        for URI references must not parse it as one).
        """
        return self.with_segments(target) if isinstance(target, str) else target

    def symlink_to(
        self,
        target: "_ty.Self | str",
        target_is_directory: bool = False,
        *,
        force: bool = False,
    ) -> None:
        """Make this path a symlink pointing to `target`.

        Signature-compatible with `pathlib.Path.symlink_to()`; `force` is a
        pathlib_next extension (see `docs/divergences.md`).

        With `force=True`, an existing entry at *this* path is removed
        first. No filesystem or transport offers an atomic "replace a
        symlink" operation, so this is an unlink-then-symlink sequence and
        is therefore **not** atomic: between the two steps the path does not
        exist, and a concurrent writer can win the race. It is a
        convenience, not a locking primitive.

        Only a non-directory entry is removed -- an existing *directory* at
        the link path is left alone and the underlying `FileExistsError`
        (or the backend's equivalent) propagates. Silently deleting a
        directory tree is never what `force=` on a symlink call is asking
        for.
        """
        # Through `_symlink_target()`, not `type(self)(target)`: for a URI-backed path
        # the constructor re-parses the string as URI syntax, cutting the target at "?"
        # or "#" and percent-decoding it.
        target = self._symlink_target(target)
        if force:
            try:
                self.unlink(missing_ok=True)
            except (IsADirectoryError, PermissionError, OSError) as error:
                # A directory at the link path (IsADirectoryError on POSIX; PermissionError or
                # an OSError on Windows and remote backends) is not removed by force=: the
                # symlink attempt below raises the error that describes the conflict.
                if not self.is_dir():
                    raise error
        return self._symlink_to(target, target_is_directory)


class Hardlink(Stat, _ty.Protocol):
    """Making a path a second name for an existing file: `_hardlink_to()` is
    the primitive; `hardlink_to()` turns a `str` target into a path of this
    backend."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (
        ("_hardlink_to", "hardlink_to"),
    )

    @_stubs.primitive
    def _hardlink_to(self, target: _ty.Self) -> None:
        """Backend primitive: make this path a hard link to `target`, a path
        of this class (a stub: `NotImplementedError`)."""
        return _stubs._unimplemented("_hardlink_to")

    def hardlink_to(self, target: "_ty.Self | str") -> None:
        """Make this path a hard link to `target` (pathlib 3.10 parity). A
        `str` is a path on this backend."""
        if isinstance(target, str):
            target = self.with_segments(target)
        return self._hardlink_to(target)
