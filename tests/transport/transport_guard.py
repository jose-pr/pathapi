"""What keeps the transport suite on this machine and independent of it.

`install_network_guard` refuses every route off the loopback interface that
Python code can take, and `isolate_environment` removes the proxy and
certificate settings a developer's shell carries. `conftest.py` applies them.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import sys

#: Variables that are not the session's own: anything ending in `_PROXY`, and
#: the single names below.
AMBIENT_NAMES = ("NETRC", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")

#: What a proxy-aware client is told never to proxy. A non-empty setting also
#: keeps `urllib` from reading the Windows registry or the macOS system
#: configuration for a proxy.
LOOPBACK_NO_PROXY = "127.0.0.1,localhost,::1"


class NetworkAccessBlocked(RuntimeError):
    """A test tried to reach a non-loopback host. Not an OSError on purpose:
    code that treats a network failure as "offline" must not swallow it."""


def is_loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").split("%", 1)[0]
    if host.lower() in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _address_host(address):
    # AF_INET/AF_INET6 tuples carry the host first; AF_UNIX paths are local.
    if isinstance(address, tuple) and address:
        return address[0]
    return None


def install_network_guard(monkeypatch, blocked):
    """Replace, for the rest of the test, every call that would send a packet
    off the loopback interface or resolve a name: it raises
    `NetworkAccessBlocked` before the original is reached, and appends a
    line to `blocked` so the test fails at teardown even if the error was
    swallowed."""

    def check(host, what):
        if not is_loopback_host(host):
            blocked.append(f"{what} {host!r}")
            raise NetworkAccessBlocked(
                f"{what} to non-loopback host {host!r}; the transport suite "
                "only talks to the loopback interface"
            )

    def wrap_method(owner, name, what, address_of):
        original = getattr(owner, name)

        def guarded(self, *args, **kwargs):
            check(_address_host(address_of(args, kwargs)), what)
            return original(self, *args, **kwargs)

        guarded.__name__ = name
        monkeypatch.setattr(owner, name, guarded)

    def wrap_function(owner, name, what, host_of):
        original = getattr(owner, name)

        def guarded(*args, **kwargs):
            check(host_of(args, kwargs), what)
            return original(*args, **kwargs)

        guarded.__name__ = name
        monkeypatch.setattr(owner, name, guarded)

    def first(args, kwargs):
        return args[0] if args else None

    def sockaddr(args, kwargs):
        return _address_host(args[0] if args else None)

    wrap_method(socket.socket, "connect", "connect", lambda a, k: first(a, k))
    wrap_method(socket.socket, "connect_ex", "connect", lambda a, k: first(a, k))
    wrap_method(
        socket.socket,
        "sendto",
        "sendto",
        lambda a, k: a[-1] if a else None,
    )
    if hasattr(socket.socket, "sendmsg"):  # absent on Windows
        wrap_method(
            socket.socket,
            "sendmsg",
            "sendto",
            lambda a, k: a[3] if len(a) > 3 else k.get("address"),
        )
    wrap_function(socket, "getaddrinfo", "getaddrinfo", first)
    wrap_function(socket, "gethostbyname", "gethostbyname", first)
    wrap_function(socket, "gethostbyname_ex", "gethostbyname", first)
    wrap_function(socket, "gethostbyaddr", "gethostbyaddr", first)
    wrap_function(socket, "getnameinfo", "getnameinfo", sockaddr)

    if sys.platform == "win32":
        # The default Windows loop connects and sends through overlapped I/O,
        # which never calls the socket methods above.
        try:
            import asyncio.windows_events as windows_events
        except ImportError:  # pragma: no cover - an unusual build
            return
        proactor = windows_events.IocpProactor
        wrap_method(
            proactor, "connect", "connect", lambda a, k: a[1] if len(a) > 1 else None
        )
        wrap_method(
            proactor,
            "sendto",
            "sendto",
            lambda a, k: a[3] if len(a) > 3 else k.get("addr"),
        )


def is_ambient_variable(name: str) -> bool:
    upper = name.upper()
    return upper.endswith("_PROXY") or upper in AMBIENT_NAMES


def isolate_environment(patch, home) -> None:
    """Make the process environment the session's own: no proxy, `.netrc` or
    CA-bundle setting of the developer's, and a home directory (`home`, a path)
    that holds nothing, so `~/.netrc` is not there to be read. `patch` is a
    `MonkeyPatch`, which restores everything when it is undone."""
    home = os.fspath(home)
    for name in list(os.environ):
        if is_ambient_variable(name):
            patch.delenv(name)
    patch.setenv("NO_PROXY", LOOPBACK_NO_PROXY)
    patch.setenv("no_proxy", LOOPBACK_NO_PROXY)
    patch.setenv("HOME", home)
    patch.setenv("USERPROFILE", home)


#: Set and cleared by pytest itself around every phase of a test.
_PYTEST_OWN = ("PYTEST_CURRENT_TEST",)


def environment_snapshot() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _PYTEST_OWN}


def changed_variables(before: dict, after: dict) -> dict:
    """`{name: (old, new)}` for every variable that differs; `None` stands
    for absent."""
    return {
        name: (before.get(name), after.get(name))
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    }


def restore_environment(snapshot: dict) -> None:
    for name in list(os.environ):
        if name not in snapshot and name not in _PYTEST_OWN:
            del os.environ[name]
    os.environ.update(snapshot)
