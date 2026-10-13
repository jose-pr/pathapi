"""What `BinaryOpen.open()` does in text mode when the `with` block of a write
stream raised.

A stream that commits on close (an upload, an archive rewrite) has a
`discard()` method. When the block ended in an exception, `discard()` is called
before the stream is closed, so the close commits nothing; when it ended
normally, or the file is closed with `close()`, nothing changes. In binary mode
the stream's own `__exit__` does this (`DiscardOnError`).
"""

from __future__ import annotations

import io as _io
import typing as _ty


class DiscardingTextIO(_io.TextIOWrapper):
    """A `TextIOWrapper` over a stream with `discard()`. Leaving a `with` block
    through an exception drops the text the wrapper still holds, calls
    `discard`, then closes: a stock wrapper would write that text to the stream
    and let the close commit it."""

    _dropping = False
    _discard: "_ty.Callable[[], object] | None" = None

    def __init__(self, stream, discard, *args, **kwargs):
        super().__init__(stream, *args, **kwargs)
        self._discard = discard

    def flush(self):
        if not self._dropping:
            super().flush()

    def close(self):
        if self.closed:
            return
        try:
            self.flush()
        except BaseException:
            # The stream has not been given all the text: it must not commit
            # what it has.
            self._dropping = True
            try:
                self._discard()
            finally:
                super().close()
            raise
        super().close()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return super().__exit__(exc_type, exc_value, traceback)
        self._dropping = True
        try:
            self._discard()
        finally:
            super().__exit__(exc_type, exc_value, traceback)


def abandon(stream: _ty.Any) -> None:
    """Close a stream whose `open()` failed after the backend gave it out,
    having it discard what it holds first so the failed call commits nothing."""
    try:
        discard = getattr(stream, "discard", None)
        if callable(discard):
            discard()
    finally:
        stream.close()
