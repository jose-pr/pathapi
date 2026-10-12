"""The status of a reply, and how much of its body the library reads."""

from __future__ import annotations

import json as _json

from ._headers import peer_text
from ._response import CHUNK, HTTPResponse


class HTTPStatusError(Exception):
    """A reply with an error status (4xx or 5xx). The path code that sent the
    request turns it into the pathlib exception for that path."""

    def __init__(self, response: HTTPResponse):
        super().__init__(response.status)
        self.response = response


def raise_for_status(resp: HTTPResponse) -> None:
    if resp.status >= 400:
        raise HTTPStatusError(resp)


def too_large(what: object, limit: int) -> str:
    return (
        f"The reply for {peer_text(what, 300)} is larger than {limit} bytes "
        "(MAX_LISTING_BYTES)"
    )


def read_limited(resp: HTTPResponse, limit: int, what: object) -> bytes:
    """The decoded body of `resp`, which must be at most `limit` bytes:
    `OSError(EFBIG)` naming `what` otherwise, with the connection closed."""
    resp._buffered(limit, too_large(what, limit))
    return resp.read()


def drop_body(resp: HTTPResponse, limit: int) -> None:
    """Gives up the body of a reply nobody looks at: it is read, up to `limit`
    bytes, so the connection can serve the next request, and `resp` is closed
    (which closes the connection when the body was longer). A failure while
    reading it is not the caller's concern: the status was already used."""
    remaining = limit
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


def json_body(resp: HTTPResponse, limit: int, what: object):
    """The JSON document in the body of `resp` (at most `limit` bytes);
    `ValueError` when it is not one."""
    return _json.loads(read_limited(resp, limit, what))
