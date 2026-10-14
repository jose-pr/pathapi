from __future__ import annotations

import io as _io
import typing as _ty
import warnings as _warnings

__all__ = ["CommitOnClose", "DiscardOnError"]


class DiscardOnError:
    """Mixin for a write stream that commits when it is closed. Leaving a
    `with` block through an exception (any `BaseException`) calls `discard()`
    and then closes; leaving it normally just closes. A stream that is
    garbage collected unclosed while `_holds_uncommitted()` is true is
    discarded too, with a `ResourceWarning`: what was never finished is not
    committed. Put it first in the bases, so that its `__exit__` and
    `__del__` win over `io.IOBase`'s."""

    __slots__ = ()

    def discard(self) -> None:
        """Drop what was written so that `close()` commits nothing. It does
        nothing more when called twice or after `close()`."""
        raise NotImplementedError

    def _holds_uncommitted(self) -> bool:
        """Whether closing now would commit something. A subclass returns
        false for a stream that has nothing to commit (never modified, or
        already discarded) so that collecting it is silent."""
        return True

    def __del__(self):
        try:
            if not self.closed and self._holds_uncommitted():
                self.discard()
                _warnings.warn(
                    f"unclosed {type(self).__name__} discarded without "
                    "committing; close the file",
                    ResourceWarning,
                    source=self,
                )
        finally:
            super().__del__()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None:
                self.discard()
        except BaseException:
            super().__exit__(exc_type, exc_value, traceback)
            raise
        return super().__exit__(exc_type, exc_value, traceback)


class CommitOnClose(DiscardOnError, _io.BytesIO):
    """A write buffer held in memory whose content is handed to `commit` when
    it is closed, once, if it was written to. For a backend with no way to
    write a file piecewise (an FTP `STOR`, a rewrite of an archive): the cost
    is the whole file in memory for the duration of the write.

    `initial` (an `open("r+")`) is the file's content, with the position at 0,
    and `commit` runs only if the buffer was modified; without it, `commit`
    runs at the first close even if nothing was written, which creates an
    empty file. `commit(buffer)` runs before the buffer is closed, so it can
    read from it.

    `discard()` drops the buffer so that a later `close()` commits nothing;
    leaving a `with` block through an exception calls it before closing, and
    so does collecting the stream unclosed, which also warns."""

    _discarded = False
    _dirty = False

    def __init__(
        self,
        commit: "_ty.Callable[[_io.BytesIO], object]",
        initial: "bytes | None" = None,
    ):
        super().__init__(b"" if initial is None else initial)
        self._commit = commit
        self._dirty = initial is None

    def write(self, data):
        self._dirty = True
        return super().write(data)

    def writelines(self, lines):
        self._dirty = True
        return super().writelines(lines)

    def truncate(self, size=None):
        self._dirty = True
        return super().truncate(size)

    def _holds_uncommitted(self):
        return self._dirty and not self._discarded

    def discard(self):
        """Drop what was written: `close()` commits nothing, whatever is
        written after this. Calling it twice, or after `close()`, does
        nothing more."""
        self._discarded = True
        if not self.closed:
            super().truncate(0)
            super().seek(0)

    def close(self):
        if self.closed:
            return
        try:
            if self._dirty and not self._discarded:
                self._commit(self)
        finally:
            # Closed even when the commit fails, so `IOBase.__del__` does not
            # retry it (over newer content) at garbage collection.
            super().close()
