"""`pathlib_next.ftp.FTPPath`: an FTP tree that needs no URI. How it is built
(an endpoint or a shared backend), what its text forms show, which paths it
treats as one tree, what it sends to the server, and what `close()` ends."""

from __future__ import annotations

import ftplib
import pickle
import socket
import subprocess
import sys
import textwrap
import urllib.parse

import pytest

from capabilities import stores_question_marks
from ftp_loopback import LoopbackFtp
from waits import wait_until

import pathlib_next
from pathlib_next.ftp import BaseFTPBackend, FTPBackend, FTPEndpoint, FTPPath
from pathlib_next.ftp import _backend as ftp_backend
from pathlib_next.testing import PathContract

FILE = "type=file;size=5;modify=20240101000000;"
DIRS = {"/", "/pub", "/pub/sub"}


@pytest.fixture
def server():
    with LoopbackFtp(
        files={"/pub/x": b"hello"},
        dirs=DIRS,
        entries={"/pub/x": FILE},
        listings={"/pub": f"{FILE} x\r\ntype=dir; sub\r\n".encode()},
    ) as running:
        yield running


def _path(server, text="/pub/x", **options):
    options.setdefault("timeout", 5)
    return FTPPath(text, host="127.0.0.1", port=server.port, **options)


def _login(server):
    return [
        (command, arg) for _, command, arg in server.log if command in ("USER", "PASS")
    ]


# --- the endpoint keywords build the path's own backend ------------------------------


def test_endpoint_keywords_log_in_with_the_given_account(server):
    path = _path(server, user="u", password="p")
    assert path.read_bytes() == b"hello"
    assert _login(server) == [("USER", "u"), ("PASS", "p")]
    assert ("RETR", "/pub/x") in [(c, a) for _, c, a in server.log]


def test_a_path_without_a_user_logs_in_anonymously(server):
    assert _path(server).stat().st_size == 5
    assert _login(server)[0] == ("USER", "anonymous")


def test_the_endpoint_keywords_are_held_by_the_backend():
    path = FTPPath(
        "/pub/x",
        host="h",
        port=2121,
        user="u",
        password="p",
        tls=True,
        timeout=3,
        verify=False,
    )
    backend = path.backend
    assert isinstance(backend, FTPBackend)
    assert (backend.host, backend.port, backend.user) == ("h", 2121, "u")
    assert (backend.password, backend.tls, backend.timeout) == ("p", True, 3)
    assert backend.verify is False
    assert backend.endpoint == FTPEndpoint("h", 2121, "u", True)


def test_the_default_port_is_21_whatever_tls_says():
    assert FTPPath("/x", host="h").backend.port == 21
    assert FTPPath("/x", host="h", tls=True).backend.port == 21


def test_a_backend_cannot_be_given_together_with_endpoint_keywords():
    backend = FTPBackend("h")
    for keywords in (
        {"host": "other"},
        {"user": "u"},
        {"password": "p"},
        {"tls": True},
        {"port": 2121},
        {"timeout": 1},
        {"verify": False},
    ):
        with pytest.raises(TypeError):
            FTPPath("/x", backend=backend, **keywords)


def test_a_path_needs_a_host_or_a_backend():
    with pytest.raises(TypeError):
        FTPPath("/x")
    with pytest.raises(TypeError):
        FTPPath("/x", user="u")


def test_building_a_path_connects_to_nothing(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(ftplib.FTP, "connect", refuse)
    path = FTPPath("/pub/x", host="h", user="u", tls=True)
    (path / "y").parent.with_name("z")
    repr(path), str(path), path.as_uri(), pickle.loads(pickle.dumps(path))
    with FTPBackend("h") as backend:
        FTPPath("/x", backend=backend)
    backend.close()


def test_a_backend_needs_a_host():
    with pytest.raises(ValueError):
        FTPBackend("")


# --- joins keep the backend ----------------------------------------------------------


def test_every_path_derived_from_one_keeps_its_backend(server):
    root = _path(server, "/pub")
    derived = [
        root / "x",
        root.joinpath("sub", "y"),
        root.parent,
        (root / "x").with_name("z"),
        (root / "x").with_suffix(".txt"),
        FTPPath(root, "x"),
        root.with_segments("a", "b"),
        *root.iterdir(),
    ]
    assert all(path.backend is root.backend for path in derived)


def test_an_explicit_backend_wins_over_the_backend_of_a_segment():
    first, second = FTPBackend("one"), FTPBackend("two")
    assert FTPPath(FTPPath("/a", backend=first), "b", backend=second).backend is second
    assert FTPPath(FTPPath("/a", backend=first), "b").backend is first


def test_paths_built_with_one_backend_share_its_connection(server):
    backend = FTPBackend("127.0.0.1", server.port, timeout=5)
    try:
        for name in ("/pub/x", "/pub/x", "/pub"):
            FTPPath(name, backend=backend).stat()
        assert server.total == 1
    finally:
        backend.close()


def test_paths_with_their_own_backends_do_not_share_a_connection(server):
    first, second = _path(server), _path(server)
    assert first.backend is not second.backend
    first.stat()
    second.stat()
    assert server.total == 2
    assert first._same_filesystem(second)


# --- text forms ----------------------------------------------------------------------


def test_str_is_the_remote_path_alone():
    path = FTPPath("/pub/x", host="h", user="u", password="p")
    assert str(path) == "/pub/x"
    assert str(path.parent) == "/pub"
    assert str(FTPPath("pub//x/./y", host="h")) == "pub/x/y"


def test_repr_names_the_endpoint_and_never_the_password():
    plain = FTPPath("/pub/x", host="h")
    assert repr(plain) == "FTPPath('/pub/x', host='h', port=21)"
    full = FTPPath("/pub/x", host="h", port=2121, user="u", password="s3cret", tls=True)
    assert repr(full) == "FTPPath('/pub/x', host='h', port=2121, user='u', tls=True)"
    assert "s3cret" not in repr(full) + repr(full.backend) + full.as_uri()


def test_repr_of_a_path_on_a_custom_backend_shows_the_backend():
    class Mine(BaseFTPBackend):
        def __repr__(self):
            return "Mine()"

    assert repr(FTPPath("/x", backend=Mine())) == "FTPPath('/x', backend=Mine())"


def test_as_uri_spells_the_endpoint_without_the_password():
    path = FTPPath("/pub/a b", host="h", user="u@x", password="p", tls=True, port=2121)
    assert path.as_uri() == "ftps://u%40x@h:2121/pub/a%20b"
    assert FTPPath("x", host="::1").as_uri() == "ftp://[::1]/x"


# --- identity ------------------------------------------------------------------------


def test_rooted_and_relative_spellings_name_one_node():
    backend = FTPBackend("h")
    spellings = [
        FTPPath(text, backend=backend)
        for text in ("pub/x", "/pub/x", "/pub//x", "/pub/./x", "/pub/sub/../x")
    ]
    keys = {path._node_key() for path in spellings}
    assert len(keys) == 1
    assert FTPPath("/pub/y", backend=backend)._node_key() not in keys


def test_two_backends_of_one_endpoint_are_one_tree():
    one = FTPPath("/x", host="h", user="u", password="a")
    other = FTPPath("/x", host="h", user="u", password="b")
    assert one.backend is not other.backend
    assert one._same_filesystem(other)
    assert one._node_key()[0] is other._node_key()[0]


@pytest.mark.parametrize(
    "keywords",
    [{"host": "other"}, {"port": 2121}, {"user": "v"}, {"tls": True}],
    ids=["host", "port", "user", "tls"],
)
def test_another_server_or_account_is_another_tree(keywords):
    base = {"host": "h", "user": "u"}
    one = FTPPath("/x", **base)
    other = FTPPath("/x", **{**base, **keywords})
    assert not one._same_filesystem(other)
    assert one._node_key()[0] is not other._node_key()[0]


def test_the_same_backend_object_is_one_tree_even_without_an_endpoint():
    class Mine(BaseFTPBackend):
        pass

    mine = Mine()
    assert FTPPath("/x", backend=mine)._same_filesystem(FTPPath("/y", backend=mine))
    assert not FTPPath("/x", backend=mine)._same_filesystem(
        FTPPath("/x", backend=Mine())
    )


def test_relative_to_refuses_a_path_of_another_server():
    one = FTPPath("/a/b", host="h")
    assert one.relative_to(FTPPath("/a", host="h")).as_posix() == "b"
    with pytest.raises(ValueError):
        one.relative_to(FTPPath("/a", host="other"))


def test_a_pickle_rebuilds_the_path_on_an_equal_backend():
    path = FTPPath("/pub/x", host="h", port=2121, user="u", tls=True)
    restored = pickle.loads(pickle.dumps(path))
    assert restored == path
    assert restored.backend is not path.backend
    assert restored.backend.endpoint == path.backend.endpoint
    assert restored.backend.password is None


def test_a_pickle_carries_the_password_only_if_the_backend_was_given_one():
    plain = pickle.dumps(FTPPath("/x", host="h", user="u"))
    assert b"s3cret" not in plain
    given = pickle.loads(
        pickle.dumps(FTPPath("/x", host="h", user="u", password="s3cret"))
    )
    assert given.backend.password == "s3cret"


def test_a_copy_shares_the_backend():
    import copy

    path = FTPPath("/x", host="h")
    assert copy.copy(path).backend is path.backend
    assert copy.deepcopy(path).backend is path.backend


# --- what is sent --------------------------------------------------------------------


def test_a_relative_path_is_sent_from_the_root(server):
    assert _path(server, "pub/x").read_bytes() == b"hello"
    assert ("RETR", "/pub/x") in [(c, a) for _, c, a in server.log]


def test_a_sibling_rename_sends_both_absolute_names(server):
    server.files["/pub/x"] = b"hello"
    moved = _path(server, "/pub/x").rename("y")
    assert moved.as_posix() == "/pub/y"
    log = [(c, a) for _, c, a in server.log]
    assert ("RNFR", "/pub/x") in log and ("RNTO", "/pub/y") in log


def test_rename_onto_another_server_is_not_implemented(server):
    source = _path(server)
    other = FTPPath("/pub/y", host="elsewhere")
    with pytest.raises(NotImplementedError):
        source.rename(other)
    assert [c for _, c, _ in server.log if c == "RNFR"] == []


# --- close() --------------------------------------------------------------------------


def _cached(backend):
    return [key for key in ftp_backend._CACHED_CLIENTS.cache if key[0] is backend]


def test_close_ends_the_connections_the_backend_holds(server):
    path = _path(server)
    path.stat()
    assert wait_until(lambda: len(server.live) == 1)
    path.backend.close()
    assert _cached(path.backend) == []
    assert wait_until(lambda: not server.live)


def test_close_is_harmless_twice_and_before_any_use():
    backend = FTPBackend("h")
    backend.close()
    backend.close()


def test_a_closed_backend_connects_afresh_on_the_next_use(server):
    path = _path(server)
    path.stat()
    path.backend.close()
    assert path.stat().st_size == 5
    assert server.total == 2
    path.backend.close()


def test_leaving_a_with_block_closes_the_connections(server):
    with FTPBackend("127.0.0.1", server.port, timeout=5) as backend:
        FTPPath("/pub/x", backend=backend).stat()
        assert _cached(backend)
    assert _cached(backend) == []
    assert wait_until(lambda: not server.live)


def test_a_backend_of_your_own_closes_and_leaves_a_with_block_the_same_way(server):
    class Mine(BaseFTPBackend):
        def client(self):
            return FTPBackend("127.0.0.1", server.port, timeout=5).client()

    with Mine() as backend:
        FTPPath("/pub/x", backend=backend).stat()
        assert _cached(backend)
        assert wait_until(lambda: len(server.live) == 1)
    assert _cached(backend) == []
    assert wait_until(lambda: not server.live)
    backend.close()


def test_close_only_ends_its_own_connections(server):
    first, second = _path(server), _path(server)
    first.stat()
    second.stat()
    first.backend.close()
    assert _cached(second.backend)
    second.backend.close()


# --- no dependency -------------------------------------------------------------------


def _run(code, blocked=()):
    prelude = "".join(f"import sys; sys.modules[{name!r}] = None\n" for name in blocked)
    source = pathlib_next.__file__.rsplit("pathlib_next", 1)[0]
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": _pythonpath(source)},
    )


def _pythonpath(source):
    import os

    return os.pathsep.join([source, *filter(None, [os.environ.get("PYTHONPATH", "")])])


def test_the_ftp_package_imports_without_the_uri_layer():
    result = _run(
        """
        import sys
        from pathlib_next.ftp import FTPPath
        loaded = [m for m, v in sys.modules.items() if v is not None
                  and m.split(".")[0] in ("uritools", "netimps")]
        loaded += [m for m in sys.modules if m.startswith("pathlib_next.uri")]
        print(loaded)
        assert FTPPath("/x", host="h").as_uri() == "ftp://h/x"
        """,
        blocked=("uritools", "netimps"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_importing_the_root_loads_neither_ftplib_nor_ssl():
    result = _run("""
        import sys
        import pathlib_next
        print(sorted(set(sys.modules) & {"ftplib", "ssl", "pathlib_next.ftp"}))
        pathlib_next.FTPPath
        print("ftplib" in sys.modules, pathlib_next.FTPPath.__module__)
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["[]", "True pathlib_next.ftp._path"]


def test_the_ftp_sources_name_no_uri_dependency():
    import pathlib

    sources = (pathlib.Path(pathlib_next.__file__).parent / "ftp").glob("*.py")
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    for forbidden in ("pathlib_next.uri", "..uri", "netimps", "uritools"):
        assert forbidden not in text


# --- the contract ---------------------------------------------------------------------


class TestFTPPathContract(PathContract):
    supports_question_mark_names = stores_question_marks()

    @pytest.fixture
    def root(self, ftp_server):
        parts = urllib.parse.urlsplit(ftp_server)
        backend = FTPBackend(
            parts.hostname,
            parts.port,
            user=parts.username,
            password=parts.password,
            timeout=10,
        )
        try:
            yield FTPPath("/", backend=backend)
        finally:
            backend.close()
