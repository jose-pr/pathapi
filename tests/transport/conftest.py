"""Fixtures of the HTTP transport suite.

The suite checks one transport per run (`--http-transports`), against servers
on the loopback interface only, and needs nothing but `pytest`, the standard
library and `pathlib_next` (the TLS tests also need `cryptography`, and skip
without it). It is shipped with `pathlib_next.http.testing` so that another
distribution can hold its transport to the same promises.
"""

import socket
import tempfile

import pytest

import transport_guard


def pytest_addoption(parser):
    parser.addoption(
        "--http-transports",
        default="builtin",
        help="the HTTP transports the transport suite runs against, by the "
        "names of pathlib_next.http.transport_names(), separated by commas "
        "(for example builtin,requests)",
    )


def pytest_configure(config):
    # The session's environment is set before the first test's snapshot of it
    # is taken, and put back when pytest exits.
    home = tempfile.TemporaryDirectory(prefix="pathlib-next-transport-home-")
    patch = pytest.MonkeyPatch()
    transport_guard.isolate_environment(patch, home.name)
    config.add_cleanup(home.cleanup)
    config.add_cleanup(patch.undo)


@pytest.fixture(autouse=True)
def _environment_unchanged():
    """A test that leaves the process environment changed fails; the
    environment is put back first so that the next test starts clean."""
    before = transport_guard.environment_snapshot()
    yield
    changed = transport_guard.changed_variables(
        before, transport_guard.environment_snapshot()
    )
    if changed:
        transport_guard.restore_environment(before)
        pytest.fail(
            "the test left the process environment changed (use monkeypatch): "
            + ", ".join(f"{k}: {old!r} -> {new!r}" for k, (old, new) in changed.items())
        )


@pytest.fixture(autouse=True)
def _block_non_loopback_network(_environment_unchanged, monkeypatch):
    """Any connect, send or name lookup aimed at a host other than loopback
    raises `NetworkAccessBlocked`, and a blocked attempt fails the test at
    teardown even if the code under test swallowed the error."""
    blocked = []
    transport_guard.install_network_guard(monkeypatch, blocked)
    yield blocked
    if blocked:
        pytest.fail(f"non-loopback network access attempted: {blocked}")


def pytest_generate_tests(metafunc):
    if "http_transport_kind" in metafunc.fixturenames:
        option = metafunc.config.getoption("--http-transports", default="builtin")
        kinds = [name.strip() for name in option.split(",") if name.strip()]
        metafunc.parametrize("http_transport_kind", kinds, indirect=True)


@pytest.fixture
def http_transport_kind(request, monkeypatch):
    """Runs the test once per HTTP transport (`--http-transports`): the
    default transport of every client the test builds is that kind
    (`PATHLIB_NEXT_HTTP_TRANSPORT`), and `transport_fakes.make_transport(kind)`
    builds one of it."""
    monkeypatch.setenv("PATHLIB_NEXT_HTTP_TRANSPORT", request.param)
    return request.param


@pytest.fixture
def http_transport(http_transport_kind):
    """A transport of the kind under test, closed after the test."""
    from transport_fakes import make_transport

    with make_transport(http_transport_kind) as transport:
        yield transport


@pytest.fixture
def unused_tcp_port():
    """A port nobody is listening on, for connection-refused coverage."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
