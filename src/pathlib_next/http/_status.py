"""The status of a reply, and how much of its body the library reads."""

from __future__ import annotations

import json as _json

from . import _settings
from ._headers import peer_text
from ._response import CHUNK, HTTPResponse


class HTTPStatusError(Exception):
    """A reply with an error status (4xx or 5xx). `response` is the reply;
    `translate_errors()` turns the error into the exception `pathlib` raises."""

    response: HTTPResponse

    def __init__(self, response: HTTPResponse):
        super().__init__(response.status)
        self.response = response


def raise_for_status(resp: HTTPResponse) -> None:
    """Raises `HTTPStatusError` for a reply with status 400 or above."""
    if resp.status >= 400:
        raise HTTPStatusError(resp)


def too_large(what: object, limit: int) -> str:
    return (
        f"The reply for {peer_text(what, 300)} is larger than {limit} bytes "
        "(MAX_REPLY_BYTES)"
    )


def read_limited(resp: HTTPResponse, what: object, limit: "int | None" = None) -> bytes:
    """The decoded body of `resp`, which must be at most `limit` bytes (default
    `MAX_REPLY_BYTES`): `OSError(EFBIG)` naming `what` otherwise, with the
    connection closed."""
    limit = _settings.MAX_REPLY_BYTES if limit is None else limit
    resp._buffered(limit, too_large(what, limit))
    return resp.read()


def drop_body(resp: HTTPResponse, limit: "int | None" = None) -> None:
    """Gives up the body of a reply nobody looks at: it is read, up to `limit`
    bytes (default `MAX_REPLY_BYTES`), so the connection can serve the next
    request, and `resp` is closed (which closes the connection when the body
    was longer). A failure while reading it is not the caller's concern: the
    status was already used."""
    remaining = _settings.MAX_REPLY_BYTES if limit is None else limit
    try:
        while remaining > 0:
            chunk = resp.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    except (OSError, ValueError):
        pass
    finally:
        resp.close()


def json_body(resp: HTTPResponse, what: object, limit: "int | None" = None):
    """The JSON document in the body of `resp` (at most `limit` bytes, default
    `MAX_REPLY_BYTES`, else `OSError(EFBIG)` naming `what`); `ValueError` when
    it is not one."""
    return _json.loads(read_limited(resp, what, limit))
