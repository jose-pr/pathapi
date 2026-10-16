"""The redirect rules of `_follow`, over an async transport."""

from __future__ import annotations

import errno as _errno

from ._async_response import AsyncHTTPResponse
from ._follow import (
    _DRAIN,
    MAX_REDIRECTS,
    RESENT_STATUSES,
    _capped,
    can_resend_body,
    is_redirect,
    origin,
    redirect_location,
    refused_redirect,
    shown,
)
from ._headers import drop_header


async def _discard(resp: AsyncHTTPResponse) -> None:
    """Gives up the rest of a body nobody wants, reading a small one so the
    connection can serve the next request."""
    try:
        await resp.read(_DRAIN)
    except OSError:
        pass
    finally:
        await resp.aclose()


async def send_following(
    transport,
    method: str,
    url: str,
    *,
    headers: "dict[str, str]",
    timeout,
    stream: bool,
    first: "str | None" = None,
    deadline=None,
) -> AsyncHTTPResponse:
    """`_follow.send_following` for an async transport."""
    home = origin(first or url)
    current = url
    hops = 0
    while True:
        if origin(current) != home:
            headers = drop_header(headers, "Authorization")
        resp = await transport.send(
            method,
            current,
            headers=headers,
            body=None,
            timeout=_capped(timeout, deadline),
            stream=stream,
        )
        if not is_redirect(resp):
            return resp
        location = redirect_location(current, resp)
        if location is None:
            await _discard(resp)
            raise refused_redirect(method, current, resp, None, "no usable Location")
        if hops >= MAX_REDIRECTS:
            await _discard(resp)
            raise OSError(
                _errno.EIO,
                f"more than {MAX_REDIRECTS} redirects from {shown(url)}",
            )
        await _discard(resp)
        if resp.status == 303 and method != "HEAD":
            method = "GET"
        current = location
        hops += 1


async def send_once(
    transport,
    method: str,
    url: str,
    *,
    headers: "dict[str, str]",
    body,
    timeout,
    stream: bool,
    deadline=None,
) -> AsyncHTTPResponse:
    """`_follow.send_once` for an async transport."""
    resp = await transport.send(
        method,
        url,
        headers=headers,
        body=body,
        timeout=_capped(timeout, deadline),
        stream=stream,
    )
    if not 300 <= resp.status < 400:
        return resp
    location = redirect_location(url, resp)
    resent = RESENT_STATUSES.get(method.upper(), (307, 308))
    if resp.status not in resent:
        reason = (
            "only "
            + ", ".join(map(str, resent[:-1]))
            + f" and {resent[-1]} send the request again"
        )
    elif location is None:
        reason = "no usable Location"
    elif origin(location) != origin(url):
        reason = "it leaves the scheme, host and port of the request"
    elif not can_resend_body(body):
        reason = "the request body cannot be sent twice"
    else:
        await _discard(resp)
        resp = await transport.send(
            method,
            location,
            headers=headers,
            body=body,
            timeout=_capped(timeout, deadline),
            stream=stream,
        )
        if not 300 <= resp.status < 400:
            return resp
        url, location = location, redirect_location(location, resp)
        reason = "a second redirect"
    await _discard(resp)
    raise refused_redirect(method, url, resp, location, reason)
