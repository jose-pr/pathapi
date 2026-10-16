"""Stand-ins for an HTTP transport, for tests that need no server: a
table-driven fake and a wrapper that records what a real one sends."""

from __future__ import annotations

import asyncio
import os
import typing

from pathapi.http import HTTPResponse, get_async_transport, get_transport

_REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    207: "Multi-Status",
    301: "Moved Permanently",
    302: "Found",
    307: "Temporary Redirect",
    404: "Not Found",
    409: "Conflict",
    500: "Internal Server Error",
    502: "Bad Gateway",
}


def current_kind():
    """The transport kind the test runs with: what `http_transport_kind` set."""
    return os.environ.get("PATHAPI_HTTP_TRANSPORT", "builtin")


def is_client_error(error):
    """Whether `error` comes from a client library (`requests`, `urllib3`)
    rather than being a builtin or pathlib exception."""
    return type(error).__module__.split(".")[0] in ("requests", "urllib3")


def make_transport(kind, **arguments):
    """A real transport of the kind `get_transport()` names."""
    return get_transport(kind)(**arguments)


def spy_on_sends(monkeypatch, project=lambda method, url, **kwargs: (method, url)):
    """A list that gains `project(method, url, **keywords)` for every request
    the real transport of the kind under test sends; the requests still go to
    the server."""
    transport_class = get_transport(current_kind())
    log = []

    def spy(self, method, url, *args, _original=transport_class.send, **kwargs):
        log.append(project(method, url, **kwargs))
        return _original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(transport_class, "send", spy)
    return log


def current_async_kind():
    """The async transport kind the test runs with: what
    `async_http_transport_kind` set."""
    return os.environ.get("PATHAPI_HTTP_ASYNC_TRANSPORT", "builtin")


def make_async_transport(kind, **arguments):
    """A real async transport of the kind `get_async_transport()` names."""
    return get_async_transport(kind)(**arguments)


def run_with_async_transport(kind, scenario, **arguments):
    """Runs the coroutine function `scenario(transport)` on a new event loop
    with an async transport of `kind`, closes the transport and returns what
    the scenario returned."""

    async def main():
        transport = make_async_transport(kind, **arguments)
        try:
            return await scenario(transport)
        finally:
            await transport.aclose()

    return asyncio.run(main())


class Sent(typing.NamedTuple):
    """One call of `send()`."""

    method: str
    url: str
    headers: dict
    body: object
    timeout: object
    stream: bool


def reply(status=200, content=b"", headers=None, reason=None, url=""):
    """An `HTTPResponse` of `status` holding `content`."""
    if reason is None:
        reason = _REASONS.get(status, "")
    return HTTPResponse(status, reason, headers or {}, url, content)


class FakeTransport:
    """Answers from `responses`, keyed `(method, url)`, or `(method, url,
    depth)` for a PROPFIND, and records every call in `sent`. A value is an
    `HTTPResponse` or a callable returning one; a call with no entry goes to
    `handler(method, url, headers=, body=)` when given, else gets `default`
    (a 404 unless given). `fail` is raised by the next call, if set."""

    def __init__(self, responses=None, default=None, handler=None):
        self.responses = dict(responses or {})
        self.default = default
        self.handler = handler
        self.sent: "list[Sent]" = []
        self.closed = 0
        self.fail: "BaseException | None" = None

    def send(self, method, url, *, headers, body=None, timeout, stream=False):
        self.sent.append(Sent(method, url, dict(headers), body, timeout, stream))
        if self.fail is not None:
            error, self.fail = self.fail, None
            raise error
        key = (method, url)
        if method == "PROPFIND":
            key = (method, url, headers.get("Depth"))
        found = self.responses.get(key, self.responses.get((method, url)))
        if found is None and self.handler is not None:
            found = self.handler(method, url, headers=headers, body=body)
        if found is None:
            found = self.default if self.default is not None else reply(404)
        response = found() if callable(found) else found
        if not response.url:
            response.url = url
        return response

    def close(self):
        self.closed += 1


class RecordingTransport:
    """Passes every call to `inner` and records it in `sent`."""

    def __init__(self, inner):
        self.inner = inner
        self.sent: "list[Sent]" = []

    def send(self, method, url, *, headers, body=None, timeout, stream=False):
        self.sent.append(Sent(method, url, dict(headers), body, timeout, stream))
        return self.inner.send(
            method, url, headers=headers, body=body, timeout=timeout, stream=stream
        )

    def close(self):
        self.inner.close()

    @property
    def methods(self):
        return [call.method for call in self.sent]

    def clear(self):
        self.sent.clear()


class FakeAsyncTransport(FakeTransport):
    """`FakeTransport` with the coroutines of an async transport."""

    async def send(self, method, url, *, headers, body=None, timeout, stream=False):
        return super().send(
            method, url, headers=headers, body=body, timeout=timeout, stream=stream
        )

    async def aclose(self):
        self.close()


def areply(status=200, content=b"", headers=None, reason=None, url=""):
    """An `AsyncHTTPResponse` of `status` holding `content`."""
    from pathapi.http import AsyncHTTPResponse

    sync = reply(status, content, headers, reason, url)
    return AsyncHTTPResponse(
        sync.status, sync.reason, sync.headers, url, content, decode=True
    )
