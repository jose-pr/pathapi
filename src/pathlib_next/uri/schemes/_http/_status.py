"""The error status of a reply, as an exception a path translates."""

from __future__ import annotations

import json as _json

from ._response import HTTPResponse


class HTTPStatusError(Exception):
    """A reply with an error status (4xx or 5xx). The path code that sent the
    request turns it into the pathlib exception for that path."""

    def __init__(self, response: HTTPResponse):
        super().__init__(response.status)
        self.response = response


def raise_for_status(resp: HTTPResponse) -> None:
    if resp.status >= 400:
        raise HTTPStatusError(resp)


def json_body(resp: HTTPResponse):
    """The JSON document in the body of `resp`; `ValueError` when it is not
    one."""
    return _json.loads(resp.read())
