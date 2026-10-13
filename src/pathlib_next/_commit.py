from __future__ import annotations

import io as _io
import typing as _ty

from ._discard import DiscardOnExit

__all__ = ["CommitOnClose"]


class CommitOnClose(DiscardOnExit, _io.BytesIO):
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
    leaving a `with` block through an exception calls it before closing."""

    _discarded = False

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
