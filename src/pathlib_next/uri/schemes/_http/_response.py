"""The response every transport returns, with the decoding they share."""

from __future__ import annotations

import errno as _errno
import typing as _ty
import zlib as _zlib

from ._headers import Headers

CHUNK = 64 * 1024
"""Most bytes read from a transport's body in one call."""

_STEP = 256 * 1024
"""Most decoded bytes one decompression step produces. The compressed bytes
that are left wait for the next step, so a body that expands a thousandfold
is never held whole because its header said it was small."""


class _Memory:
    """A body that is already in memory."""

    def __init__(self, data):
        self._data = memoryview(bytes(data))
        self._at = 0

    def read(self, amount: int) -> bytes:
        chunk = bytes(self._data[self._at : self._at + amount])
        self._at += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class _Inflater:
    """Decompresses one `gzip` or `deflate` body in bounded steps. `deflate` is
    read as zlib-wrapped data, or as raw data when the zlib header is absent,
    which is what servers send under that name; concatenated gzip members are
    one body."""

    def __init__(self, gzip: bool):
        self._gzip = gzip
        self._object = None
        self._input = b""
        self._members = 0

    @property
    def has_input(self) -> bool:
        return bool(self._input)

    def feed(self, data: bytes) -> None:
        self._input += data

    def _new(self, data: bytes):
        if self._gzip:
            return _zlib.decompressobj(16 + _zlib.MAX_WBITS)
        wrapped = len(data) < 2 or (
            data[0] & 0x0F == 8 and ((data[0] << 8) | data[1]) % 31 == 0
        )
        return _zlib.decompressobj(_zlib.MAX_WBITS if wrapped else -_zlib.MAX_WBITS)

    def step(self) -> bytes:
        data = self._input
        if self._object is None:
            self._object = self._new(data)
        try:
            out = self._object.decompress(data, _STEP)
        except _zlib.error:
            if self._members:
                # Bytes after the last complete member are not a body.
                self._input, self._object = b"", None
                return b""
            kind = "gzip" if self._gzip else "deflate"
            raise OSError(
                _errno.EIO, f"the response body is not valid {kind} data"
            ) from None
        if self._object.eof:
            self._members += 1
            self._input = self._object.unused_data
            self._object = None
        else:
            self._input = self._object.unconsumed_tail
            if not out and len(self._input) >= len(data):
                self._input = b""
        return out

    def finish(self) -> None:
        if self._object is not None:
            raise OSError(_errno.EIO, "the compressed response body ended early")


def _inflater_for(headers: Headers) -> "_Inflater | None":
    names = [
        name.strip().lower()
        for name in headers.get("Content-Encoding", "").split(",")
        if name.strip().lower() not in ("", "identity")
    ]
    if len(names) != 1:
        return None
    if names[0] in ("gzip", "x-gzip"):
        return _Inflater(gzip=True)
    if names[0] == "deflate":
        return _Inflater(gzip=False)
    return None


class HTTPResponse:
    """One response: `status`, `reason`, `headers` (a read-only mapping that
    ignores the case of a name), `url` (the URL the request went to) and the
    body, read with `read()` or `iter_bytes()`. A context manager that closes.

    The body is decoded when the response says it is `gzip` or `deflate`
    encoded, unless the request set its own `Accept-Encoding`; `headers` are
    the headers as received, so `Content-Length` counts the encoded bytes. A
    body that ends before the length the server stated is `OSError(EIO)`, never
    a shorter body that reads as complete.

    A transport builds one with `body` as `bytes`, or as an object with
    `read(amount) -> bytes` (empty at the end) and `close()`, and
    `decode=False` for a body it already decoded or that was not asked for
    in a coding."""

    def __init__(
        self,
        status: int,
        reason: str,
        headers: "_ty.Mapping[str, str] | _ty.Iterable[tuple[str, str]]",
        url: str,
        body: "bytes | _ty.Any" = b"",
        *,
        decode: bool = True,
    ):
        self._closed = False
        self._ended = False
        self.status = int(status)
        self.reason = reason or ""
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)
        self.url = url
        self._kept = False
        if isinstance(body, (bytes, bytearray, memoryview)):
            body = _Memory(body)
            self._kept = True  # in memory: readable after close
        self._source = body
        self._inflater = _inflater_for(self.headers) if decode else None
        self._leftover = b""

    def __repr__(self) -> str:
        return f"HTTPResponse({self.status}, {self.reason!r})"

    def __enter__(self) -> "HTTPResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _check_open(self) -> None:
        if self._closed and not self._kept:
            raise ValueError("read of a closed response")

    def _next(self, limit: int = CHUNK) -> bytes:
        """The next decoded bytes of the body; empty at its end. Without a
        coding, no more than `limit` bytes are asked of the transport."""
        if self._ended:
            return b""
        self._check_open()
        inflater = self._inflater
        while True:
            if inflater is not None and inflater.has_input:
                out = inflater.step()
                if out:
                    return out
                continue
            out = self._source.read(CHUNK if inflater else min(CHUNK, limit))
            if not out:
                if inflater is not None:
                    inflater.finish()
                self._ended = True
                return b""
            if inflater is None:
                return out
            inflater.feed(out)

    def read(self, size: "int | None" = -1) -> bytes:
        """Up to `size` bytes of the decoded body, fewer only at its end; the
        whole rest of it when `size` is negative or `None`."""
        self._check_open()
        parts = []
        have = 0
        while size is None or size < 0 or have < size:
            wanted = CHUNK if size is None or size < 0 else size - have
            chunk = self._leftover or self._next(wanted)
            if not chunk:
                break
            if size is not None and 0 <= size < have + len(chunk):
                self._leftover = chunk[size - have :]
                chunk = chunk[: size - have]
            else:
                self._leftover = b""
            parts.append(chunk)
            have += len(chunk)
        return b"".join(parts)

    def iter_bytes(self, chunk_size: int = CHUNK) -> "_ty.Iterator[bytes]":
        """The rest of the body in pieces of at most `chunk_size` bytes, each
        as soon as it has arrived."""
        self._check_open()
        while True:
            chunk = self._leftover or self._next(chunk_size)
            if not chunk:
                return
            self._leftover = chunk[chunk_size:]
            yield chunk[:chunk_size]

    def close(self) -> None:
        """Releases the connection. Harmless when called again. A body that was
        not read to its end is abandoned, and its connection is closed."""
        if not getattr(self, "_closed", True):
            self._closed = True
            self._source.close()

    def _buffered(self) -> "HTTPResponse":
        """Reads the rest of the body into memory and closes the source, so the
        response can be read after the connection has been given back."""
        try:
            self._leftover = self.read()
            self._kept = True
        finally:
            self.close()
        return self
