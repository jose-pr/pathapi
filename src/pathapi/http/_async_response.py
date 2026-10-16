"""The response every async transport returns, read with coroutines."""

from __future__ import annotations

import errno as _errno
import typing as _ty

from ._headers import Headers
from ._response import CHUNK, _inflater_for


class _Memory:
    """A body that is already in memory."""

    def __init__(self, data):
        self._data = memoryview(bytes(data))
        self._at = 0

    async def read(self, amount: int) -> bytes:
        chunk = bytes(self._data[self._at : self._at + amount])
        self._at += len(chunk)
        return chunk

    async def aclose(self) -> None:
        pass


class AsyncHTTPResponse:
    """One response, as `HTTPResponse` is, with the body read by coroutines:
    `status`, `reason`, `headers`, `url`, `await read()`, `iter_bytes()` (an
    async iterator) and `await aclose()`. An async context manager that closes.

    Decoding, the error for a body that ends early and the rule for reading
    a closed response are those of `HTTPResponse`.

    A transport builds one with `body` as `bytes`, or as an object with
    `async read(amount) -> bytes` (empty at the end) and `async aclose()`,
    and `decode=False` for a body it already decoded or that was not asked
    for in a coding."""

    status: int
    reason: str
    headers: Headers
    url: str

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
        return f"AsyncHTTPResponse({self.status}, {self.reason!r})"

    async def __aenter__(self) -> "AsyncHTTPResponse":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    def _check_open(self) -> None:
        if self._closed and not self._kept:
            raise ValueError("read of a closed response")

    async def _next(self, limit: int = CHUNK) -> bytes:
        """The next decoded bytes of the body; empty at its end."""
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
            out = await self._source.read(CHUNK if inflater else min(CHUNK, limit))
            if not out:
                if inflater is not None:
                    inflater.finish()
                self._ended = True
                return b""
            if inflater is None:
                return out
            inflater.feed(out)

    async def read(self, size: "int | None" = -1) -> bytes:
        """Up to `size` bytes of the decoded body, fewer only at its end; the
        whole rest of it when `size` is negative or `None`."""
        self._check_open()
        parts = []
        have = 0
        while size is None or size < 0 or have < size:
            wanted = CHUNK if size is None or size < 0 else size - have
            chunk = self._leftover or await self._next(wanted)
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

    async def iter_bytes(self, chunk_size: int = CHUNK) -> "_ty.AsyncIterator[bytes]":
        """The rest of the body in pieces of at most `chunk_size` bytes, each
        as soon as it has arrived."""
        self._check_open()
        while True:
            chunk = self._leftover or await self._next(chunk_size)
            if not chunk:
                return
            self._leftover = chunk[chunk_size:]
            yield chunk[:chunk_size]

    async def aclose(self) -> None:
        """Releases the connection. Harmless when called again. A body that was
        not read to its end is abandoned, and its connection is closed."""
        if not self._closed:
            self._closed = True
            await self._source.aclose()

    async def buffered(self) -> "AsyncHTTPResponse":
        """Reads the rest of the body into memory, closes the connection and
        returns this response, which can then be read after it is closed. A
        transport returns its response this way when `send()` is called with
        `stream=False`."""
        return await self._buffered()

    async def _buffered(
        self, limit: "int | None" = None, message: str = ""
    ) -> "AsyncHTTPResponse":
        """Reads the rest of the body into memory and closes the source. A
        decoded body longer than `limit` bytes is `OSError(EFBIG)` with
        `message`, and its connection is closed."""
        try:
            data = await self.read(-1 if limit is None else limit + 1)
            if limit is not None and len(data) > limit:
                raise OSError(_errno.EFBIG, message or f"larger than {limit} bytes")
            self._leftover = data
            self._kept = True
        finally:
            await self.aclose()
        return self
