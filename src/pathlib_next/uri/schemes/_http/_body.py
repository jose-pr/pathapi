"""How a request body is framed: the length it is sent under, and a body that
sends exactly that many bytes."""

from __future__ import annotations

import errno as _errno
import os as _os
import stat as _stat
import typing as _ty

_STEP = 65536


def body_length(body) -> "int | None":
    """The bytes `body` will send from now on, when that is known without
    reading it, else `None`: a bytes-like object, a list or tuple of bytes, a
    file that is seekable (from `tell()` to its end, the position restored) or
    is a regular file (its size less `tell()`). An iterator, a generator and a
    stream that cannot report its size have no length."""
    if body is None:
        return 0
    if isinstance(body, (bytes, bytearray, memoryview)):
        return memoryview(body).nbytes
    if isinstance(body, (list, tuple)):
        if all(isinstance(piece, (bytes, bytearray)) for piece in body):
            return sum(len(piece) for piece in body)
        return None
    if hasattr(body, "read"):
        return _file_length(body)
    return None


def _file_length(file) -> "int | None":
    try:
        seekable = file.seekable()
    except (AttributeError, ValueError, OSError):
        seekable = False
    if seekable:
        here = file.tell()
        try:
            end = file.seek(0, 2)
            if end is None:
                end = file.tell()
        finally:
            file.seek(here)
        return max(0, end - here)
    try:
        info = _os.fstat(file.fileno())
        if _stat.S_ISREG(info.st_mode):
            return max(0, info.st_size - file.tell())
    except (AttributeError, ValueError, OSError):
        pass
    return None


def declared_length(pairs: "_ty.Iterable[tuple[str, str]]") -> "int | None":
    """The `Content-Length` the caller set, or `None`; `ValueError` for one
    that is repeated or is not a count of bytes."""
    found = [value for name, value in pairs if name.lower() == "content-length"]
    if not found:
        return None
    value = found[0].strip()
    if len(found) > 1 or not (value.isascii() and value.isdigit()):
        raise ValueError("the Content-Length header is not one count of bytes")
    return int(value)


class Sized:
    """A body sent under a `Content-Length`: exactly `length` bytes of `source`
    (a binary file or an iterable of bytes). It never reads or sends more, so a
    file that grew since it was measured cannot overrun the length, and a
    source that ends first is `OSError(EIO)`, not a shorter upload."""

    def __init__(self, source, length: int):
        self._length = length
        self._left = length
        if hasattr(source, "read"):
            self._read = source.read
        else:
            self._pieces = iter(source)
            self._pending = b""
            self._read = self._read_pieces

    def __len__(self) -> int:
        return self._length

    def __iter__(self) -> "_ty.Iterator[bytes]":
        while True:
            chunk = self.read(_STEP)
            if not chunk:
                return
            yield chunk

    def read(self, amount: "int | None" = -1) -> bytes:
        if self._left <= 0:
            return b""
        want = self._left if amount is None or amount < 0 else min(amount, self._left)
        chunk = self._read(want)
        if isinstance(chunk, str):
            raise TypeError("the request body is bytes, not text")
        if not chunk:
            raise OSError(
                _errno.EIO, "the request body ended before its Content-Length"
            )
        chunk = bytes(chunk[:want])
        self._left -= len(chunk)
        return chunk

    def _read_pieces(self, want: int) -> bytes:
        while not self._pending:
            try:
                self._pending = bytes(next(self._pieces))
            except StopIteration:
                return b""
        chunk, self._pending = self._pending[:want], self._pending[want:]
        return chunk


def _chunks(file) -> "_ty.Iterator[bytes]":
    while True:
        chunk = file.read(_STEP)
        if not chunk:
            return
        yield chunk


def prepare_body(body, pairs: "list[tuple[str, str]]"):
    """`(value, length)`: what to hand the HTTP layer as the body, and the
    `Content-Length` to add (`None` when the request already names one, the
    layer adds its own, or none is needed). A length the caller set is
    trusted, so nothing is chunked and exactly that many bytes are sent;
    otherwise a body whose length is known is sent under it and any other is
    left for the layer to send chunked."""
    if body is None:
        return None, None
    declared = declared_length(pairs)
    known = body_length(body)
    if isinstance(body, (bytes, bytearray, memoryview)):
        if declared is None or declared == known:
            return body, None
        if declared > known:
            raise ValueError("the Content-Length header is larger than the body")
        return bytes(memoryview(body)[:declared]), None
    if declared is not None:
        if known is not None and declared > known:
            raise ValueError("the Content-Length header is larger than the body")
        length, add = declared, None
    elif any(name.lower() == "transfer-encoding" for name, _ in pairs):
        return body, None  # the caller frames it
    else:
        length, add = known, known
    if length is None:
        return (_chunks(body) if hasattr(body, "read") else body), None
    if length == 0:
        return b"", add
    return Sized(body, length), add
