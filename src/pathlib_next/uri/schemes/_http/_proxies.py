"""Which proxies a request goes through, from the environment or a provider."""

from __future__ import annotations

import base64 as _base64
import errno as _errno
import ipaddress as _ipaddress
import typing as _ty
import urllib.parse as _urlparse
import urllib.request as _urlrequest

REQUESTS_HINT = "use RequestsTransport for it"


class ProxyAddress:
    """An `http://` proxy: where to connect, and the `Proxy-Authorization`
    value built from the credentials in its URL. Neither the credentials nor
    the URL are in `repr()` or in any message."""

    __slots__ = ("host", "port", "authorization")

    def __init__(self, host: str, port: int, authorization: "str | None"):
        self.host = host
        self.port = port
        self.authorization = authorization

    @property
    def key(self) -> "tuple[str, int]":
        return (self.host, self.port)

    def __repr__(self) -> str:
        return f"ProxyAddress({self.host!r}, {self.port})"


def _address(proxy: object) -> ProxyAddress:
    """The address a proxy names: a URL, or any object with a `url`."""
    url = proxy if isinstance(proxy, str) else getattr(proxy, "url", None)
    if not isinstance(url, str) or not url.strip():
        raise ValueError("a proxy is a URL or an object with a url")
    url = url.strip()
    parts = _urlparse.urlsplit(url if "://" in url else "http://" + url)
    scheme = parts.scheme.lower()
    if scheme != "http":
        raise OSError(
            _errno.EPROTONOSUPPORT,
            f"the built-in HTTP transport has no {scheme!r} proxy support; "
            + REQUESTS_HINT,
        )
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError("a proxy URL cannot be parsed") from None
    if not host:
        raise ValueError("a proxy URL has no host")
    user, password = parts.username, parts.password
    if user is None and not isinstance(proxy, str):
        user = getattr(proxy, "username", None) or None
        password = getattr(proxy, "password", None) or None
    authorization = None
    if user is not None:
        raw = _urlparse.unquote(user) + ":" + _urlparse.unquote(password or "")
        authorization = "Basic " + _base64.b64encode(raw.encode("utf-8")).decode(
            "ascii"
        )
    return ProxyAddress(host, port or 80, authorization)


def _from_provider(found: object) -> "list[ProxyAddress | None]":
    if found is None:
        return [None]
    if isinstance(found, str) or hasattr(found, "url"):
        return [_address(found)]
    try:
        items = list(found)
    except TypeError:
        raise ValueError("a proxy is a URL or an object with a url") from None
    chain = [None if item is None else _address(item) for item in items]
    return chain or [None]


def _in_no_proxy_network(host: str, no_proxy: str) -> bool:
    """Whether `host`, an IP literal, is inside a CIDR block (`10.0.0.0/8`,
    `fd00::/8`) listed in `no_proxy`: the form `requests` honours and
    `urllib.request.proxy_bypass()` does not."""
    try:
        address = _ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    for entry in no_proxy.split(","):
        if "/" not in entry:
            continue
        try:
            network = _ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            continue
        if address.version == network.version and address in network:
            return True
    return False


def _lookup(provider: _ty.Any, url: str, scheme: str, host: str) -> object:
    """`provider[url]`; for a plain `dict`, the keys `requests` uses for its
    `proxies` too (`scheme://host`, `scheme`, `all://host`, `all`), so a
    mapping written for it is not silently ignored."""
    try:
        return provider[url]
    except KeyError:
        if not isinstance(provider, dict):
            raise
    for key in (f"{scheme}://{host}", scheme, f"all://{host}", "all"):
        if key in provider:
            return provider[key]
    raise KeyError(url)


def proxies_for(
    provider: _ty.Any, url: str, scheme: str, host: str
) -> "list[ProxyAddress | None]":
    """The proxies to try for `url`, in order; `None` stands for no proxy.

    Without a `provider` the environment decides (`urllib.request.getproxies()`
    and `proxy_bypass()`, and the CIDR blocks of `NO_PROXY` for an IP-literal
    host). A provider is indexed with the URL: `KeyError` means no proxy, and
    the environment is not asked; an empty answer is no proxy too."""
    if provider is None:
        chosen = _urlrequest.getproxies()
        value = chosen.get(scheme) or chosen.get("all")
        if not value:
            return [None]
        try:
            if _urlrequest.proxy_bypass(host):
                return [None]
        except Exception:
            pass  # a bypass rule that cannot be read does not send it direct
        if _in_no_proxy_network(host, chosen.get("no", "")):
            return [None]
        return [_address(value)]
    try:
        found = _lookup(provider, url, scheme, host)
    except KeyError:
        return [None]
    return _from_provider(found)
