"""Making a path absolute and following the links on its way."""

from __future__ import annotations

import errno as _errno
import stat as _stat
import sys as _sys
import typing as _ty

from .. import _stubs
from ._support import supports
from .links import Readlink

__all__ = ["Resolve"]

#: Links followed in one `resolve()` before it reports a loop.
_MAX_LINKS = 40


class Resolve(Readlink, _ty.Protocol):
    """`absolute()` and `resolve()`. `_cwd()` is the primitive: the directory
    a relative path is relative to. `resolve()` follows links through
    `readlink()` where the class has it and is lexical otherwise."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (("_cwd", "absolute"),)

    @_stubs.primitive
    def _cwd(self) -> _ty.Self:
        """The directory a relative path is relative to, as a path of this
        class (a stub: `NotImplementedError`)."""
        return _stubs._unimplemented("_cwd")

    def absolute(self) -> _ty.Self:
        """This path when it is absolute, else `_cwd()` joined with it."""
        if self.is_absolute():
            return self
        return self._cwd().joinpath(self)

    def resolve(self, strict: bool = False) -> _ty.Self:
        """The absolute path with `..` applied and every link replaced by what
        `readlink()` says, at most 40 of them. `strict=True` raises
        `FileNotFoundError` for a component that is missing; otherwise the
        rest of the names are kept as they are. A link loop raises what
        `pathlib` raises on this interpreter: `RuntimeError` before 3.13,
        `OSError(ELOOP)` from 3.13 when `strict` (otherwise the looping link
        is kept)."""
        path = self.absolute()
        links = supports(self, Readlink)
        root = path.with_segments(path.anchor or "/")
        resolved = root
        pending = [name for name in path.segments if name][::-1]
        followed = 0
        while pending:
            name = pending.pop()
            if name == ".":
                continue
            if name == "..":
                resolved = resolved.parent
                continue
            candidate = resolved.joinpath(name)
            if not links:
                if strict:
                    candidate.stat()
                resolved = candidate
                continue
            try:
                mode = candidate.stat(follow_symlinks=False).st_mode
            except OSError:
                if strict:
                    raise
                resolved = candidate
                continue
            if not _stat.S_ISLNK(mode):
                resolved = candidate
                continue
            followed += 1
            if followed > _MAX_LINKS:
                if _sys.version_info < (3, 13):
                    raise RuntimeError(f"Symlink loop from {candidate!r}")
                if strict:
                    raise OSError(
                        _errno.ELOOP,
                        "Too many levels of symbolic links",
                        str(candidate),
                    )
                resolved = candidate
                continue
            target = candidate.readlink()
            text = getattr(target, "path", None)
            if not isinstance(text, str):
                text = target.as_posix()
            if text.startswith("/"):
                resolved = root
            pending.extend(part for part in reversed(text.split("/")) if part)
        return resolved
