import contextlib
import functools
import http.server
import socket
import tempfile
import threading
import time

import pytest

import census as census_module
import hermetic
from hermetic import NetworkAccessBlocked  # noqa: F401  (re-exported for tests)
from pathapi.mempath import MemPath, MemPathBackend

try:
    from hypothesis import settings as _hypothesis_settings
except ImportError:  # hypothesis is a dev extra
    pass
else:
    # A property that is slow on a busy runner is not a failure.
    _hypothesis_settings.register_profile("pathapi", deadline=None)
    _hypothesis_settings.load_profile("pathapi")


@pytest.fixture
def fixture_tree(tmp_path):
    """A small fixture tree for glob/walk/contract tests:

    root/
      a.txt
      b.py
      .hidden.txt
      sub/
        c.py
        nested/
          d.py
      empty_dir/

    Built by the shipped `pathapi.testing.populate_fixture_tree()`, so
    the in-repo contracts and downstream users share one definition.
    """
    from pathapi.testing import populate_fixture_tree

    return populate_fixture_tree(tmp_path)


@contextlib.contextmanager
def _serve_http(handler):
    """Run a `ThreadingHTTPServer` for `handler` on an OS-assigned loopback
    port; yields its base URL (no trailing slash) and stops it on exit."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # A short poll interval: `shutdown()` waits up to one for the loop to see it.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def http_server(fixture_tree):
    """Serve fixture_tree over HTTP via stdlib's ThreadingHTTPServer with
    directory-listing support, on an OS-assigned port. Yields the base URL
    (no trailing slash)."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(fixture_tree)
    )
    with _serve_http(handler) as base_url:
        yield base_url


def pytest_addoption(parser):
    parser.addoption(
        "--leak-census",
        nargs="?",
        const="report",
        choices=("report", "fail"),
        default=None,
        help="at the end of the session, list the sockets, event loops, "
        "subprocesses and threads still alive; `fail` also exits non-zero "
        "(add python -X tracemalloc=25 to name where each was created)",
    )


_SESSION = pytest.StashKey[object]()


def pytest_sessionstart(session):
    session.config.stash[_SESSION] = session


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    mode = config.getoption("--leak-census")
    if not mode:
        return
    census = census_module.take_census()
    terminalreporter.section("leak census")
    if not census.items:
        terminalreporter.write_line("nothing is still alive")
        return
    for line in census.lines():
        terminalreporter.write_line(line)
    terminalreporter.write_line(
        f"{len(census.items)} alive, {len(census.library_owned())} created by "
        "pathapi (known only under -X tracemalloc)"
    )
    if mode == "fail" and exitstatus == 0:
        # The summary runs inside `pytest_sessionfinish`; the process exits
        # with the status the session holds afterwards.
        config.stash[_SESSION].exitstatus = 1


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: lift the autouse non-loopback network guard for a test "
        "that deliberately resolves or connects to a real host",
    )
    config.addinivalue_line(
        "markers",
        "allow_program(*names): let a test start these programs; any other "
        "except the interpreter and the host probes raises ProcessSpawnBlocked",
    )
    # The session's environment is set before the first test's snapshot of it
    # is taken, and put back when pytest exits.
    home = tempfile.TemporaryDirectory(prefix="pathapi-home-")
    patch = pytest.MonkeyPatch()
    hermetic.isolate_environment(patch, home.name)
    config.add_cleanup(home.cleanup)
    config.add_cleanup(patch.undo)


_ENV_BEFORE = pytest.StashKey[dict]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    item.stash[_ENV_BEFORE] = hermetic.environment_snapshot()
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    result = yield
    before = item.stash.get(_ENV_BEFORE, None)
    changed = (
        hermetic.changed_variables(before, hermetic.environment_snapshot())
        if before is not None
        else {}
    )
    if changed:
        hermetic.restore_environment(before)
        raise AssertionError(
            "the test left the process environment changed (use monkeypatch): "
            + ", ".join(f"{k}: {old!r} -> {new!r}" for k, (old, new) in changed.items())
        )
    return result


@pytest.fixture(autouse=True)
def _block_non_loopback_network(request, monkeypatch):
    """Hermetic tests: any connect/sendto/name lookup aimed at a host other
    than loopback raises `NetworkAccessBlocked`, a program other than the
    interpreter raises `ProcessSpawnBlocked`, and a blocked attempt fails the
    test at teardown even if the code under test swallowed the error.
    Autouse rather than opt-in, because the failure it prevents (a refactor
    that quietly sends a monkeypatched HTTP test to the real internet, or a
    DNS lookup that stalls an offline CI runner) is only caught by a guard
    the test author did not have to remember. `@pytest.mark.allow_network`
    lifts the network half for a test that means to use the network."""
    blocked = []
    if not request.node.get_closest_marker("allow_network"):
        hermetic.install_network_guard(monkeypatch, blocked)
    allowed = [
        name
        for marker in request.node.iter_markers("allow_program")
        for name in marker.args
    ]
    hermetic.install_process_guard(monkeypatch, blocked, allowed)
    # Yielded so a test of the guard itself can inspect and clear it.
    yield blocked
    if blocked:
        pytest.fail(f"non-loopback network access attempted: {blocked}")


@pytest.fixture(autouse=True)
def _close_cached_ftp_connections():
    """The FTP connection cache outlives a test, but the loopback server a
    test started does not: close what the test left in it, so no socket
    waits for the interpreter to exit."""
    yield
    import sys

    ftp = sys.modules.get("pathapi.ftp._backend")
    if ftp is not None:
        for key in list(ftp._CACHED_CLIENTS.cache):
            ftp._CACHED_CLIENTS.discard(*key)


@pytest.fixture
def mem_backend():
    return MemPathBackend()


@pytest.fixture
def mem_root(mem_backend):
    return MemPath("/", backend=mem_backend)


@pytest.fixture
def http_status_server():
    """Serves canned status codes / delays on demand, driving
    `_translate_http_errors` from real `requests` exceptions instead of
    hand-constructed ones: GET/HEAD/PUT/DELETE `/<code>` returns that
    status; `/timeout` sleeps past any client-side timeout. Yields the
    base URL (no trailing slash)."""

    class _StatusHandler(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            path = self.path.lstrip("/")
            if path == "timeout":
                time.sleep(5)
                return
            try:
                code = int(path)
            except ValueError:
                code = 404
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_HEAD = do_PUT = do_DELETE = _handle

        def log_message(self, format, *args):
            pass

    with _serve_http(_StatusHandler) as base_url:
        yield base_url


@pytest.fixture
def unused_tcp_port():
    """A port nobody is listening on, for connection-refused coverage."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def ftp_server(fixture_tree):
    pytest.importorskip("pyftpdlib")
    import errno
    import threading
    import warnings

    with warnings.catch_warnings():
        # pyftpdlib still imports asyncore/asynchat on Python 3.10/3.11,
        # which we don't control and which should not fail our own test
        # suite under global "warnings as errors".
        warnings.filterwarnings(
            "ignore",
            message=r".*asyncore module is deprecated.*",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*asynchat module is deprecated.*",
            category=DeprecationWarning,
        )
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.ioloop import Select
        from pyftpdlib.servers import FTPServer

    # pyftpdlib's MFMT calls datetime.utcfromtimestamp(), deprecated since
    # 3.12: under "warnings as errors" its server thread would raise and drop
    # the connection. The filter lasts for the test that uses the fixture.
    warnings.filterwarnings(
        "ignore",
        message=r".*utcfromtimestamp.*",
        category=DeprecationWarning,
    )

    authorizer = DummyAuthorizer()
    authorizer.add_user("user", "12345", str(fixture_tree), perm="elradfmwMT")

    class Handler(FTPHandler):
        pass

    Handler.authorizer = authorizer
    handler = Handler
    # Force the portable select-based loop for tests. pyftpdlib's macOS
    # kqueue loop can emit an unhandled thread exception if the server fd is
    # closed from the pytest thread while the server thread is polling it.
    ioloop = Select()
    server = FTPServer(("127.0.0.1", 0), handler, ioloop=ioloop)

    stop = threading.Event()

    def serve():
        # serve_forever()'s own loop, with a poll timeout and a stop flag:
        # without a timeout select() blocks indefinitely, so a close
        # scheduled from the pytest thread never ran and every teardown
        # waited out its full join timeout. Everything, close_all()
        # included, runs on this thread.
        try:
            while not stop.is_set() and ioloop.socket_map:
                ioloop.poll(0.05)
                ioloop.sched.poll()
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        finally:
            server.close_all()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        port = server.socket.getsockname()[1]
        yield f"ftp://user:12345@127.0.0.1:{port}/"
    finally:
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive(), "ftp_server thread did not stop"
