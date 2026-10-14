"""pathlib parity and soundness of `FTPPath`, end to end: loopback pyftpdlib
servers built here, with per-test handler settings. No external hosts."""

import errno
import ftplib
import gc
import io
import os
import stat
import threading
import time
import urllib.parse
import warnings

import pytest

from waits import wait_until

from pathlib_next import LocalPath
from pathlib_next.ftp import FTPBackend, FTPPath
from pathlib_next.ftp import _backend as ftp_mod
from pathlib_next.ftp._facts import facts_to_filestat


def _backend(url, **options):
    """The backend of the server a `ftp://user:password@host:port/` URL names."""
    parts = urllib.parse.urlsplit(url)
    options.setdefault("timeout", 5)
    return FTPBackend(
        parts.hostname,
        parts.port,
        user=parts.username,
        password=parts.password,
        **options,
    )


def _at(url, path="", backend=None):
    """The path `path` on the server `url` names; the path of the URL itself
    is not read."""
    return FTPPath("/" + path.lstrip("/"), backend=backend or _backend(url))


# --- FTP loopback servers ------------------------------------------------------


@pytest.fixture
def ftp_factory(tmp_path):
    """`start(perm=..., mlsd=..., timeout=...)` -> (url, handler class, root).
    The handler counts the control connections it accepted."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.ioloop import Select
        from pyftpdlib.servers import FTPServer

    started = []

    def start(perm="elradfmwMT", mlsd=True, timeout=None):
        root = tmp_path / f"root{len(started)}"
        (root / "d" / "sub").mkdir(parents=True)
        (root / "d" / "f.txt").write_bytes(b"hello")
        authorizer = DummyAuthorizer()
        authorizer.add_user("user", "12345", str(root), perm=perm)

        class Handler(FTPHandler):
            connections = 0
            disconnections = 0

            def on_connect(self):
                type(self).connections += 1

            def on_disconnect(self):
                type(self).disconnections += 1

        Handler.authorizer = authorizer
        if not mlsd:
            Handler.proto_cmds = {
                name: spec
                for name, spec in FTPHandler.proto_cmds.items()
                if name not in ("MLSD", "MLST")
            }
        if timeout is not None:
            Handler.timeout = timeout
        ioloop = Select()
        server = FTPServer(("127.0.0.1", 0), Handler, ioloop=ioloop)

        def serve():
            try:
                server.serve_forever()
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        port = server.socket.getsockname()[1]
        started.append((server, ioloop, thread, port))
        return f"ftp://user:12345@127.0.0.1:{port}/", Handler, root

    yield start
    for server, ioloop, thread, port in started:
        # Close this test's cached connections before their server goes.
        for key in list(ftp_mod._CACHED_CLIENTS.cache):
            if key[1].port == port:
                ftp_mod._CACHED_CLIENTS.discard(*key)
        ioloop.call_later(0, server.close_all)
        thread.join(timeout=5)


def test_ftp_paths_built_with_one_backend_share_one_connection(ftp_factory):
    url, handler, root = ftp_factory()
    for i in range(10):
        (root / f"f{i}.txt").write_bytes(b"%d" % i)
    backend = _backend(url)
    for i in range(10):
        assert _at(url, f"f{i}.txt", backend).read_bytes() == b"%d" % i
    assert handler.connections == 1


def test_ftp_evicted_connection_is_closed(ftp_factory, monkeypatch):
    url, _handler, _root = ftp_factory()
    p = _at(url, "d/f.txt")
    p.read_bytes()
    ((key, client),) = [
        (k, c)
        for k, c in ftp_mod._CACHED_CLIENTS.cache.items()
        if k[1] == p.backend.endpoint
    ]
    ftp_mod._CACHED_CLIENTS.discard(*key)
    assert client.sock is None  # ftplib.FTP.close() clears it


class _Interrupt(BaseException):
    """Stands in for a KeyboardInterrupt (or a decode error) raised in the
    middle of a transfer."""


def test_ftp_failed_transfer_does_not_desync_the_next_operation(
    ftp_factory, monkeypatch
):
    url, _handler, root = ftp_factory()
    (root / "small.bin").write_bytes(b"x" * 100)
    base_cls = ftplib.FTP

    class InterruptOnce(base_cls):
        armed = True

        def retrbinary(self, cmd, callback, *args, **kwargs):
            def interrupting(data):
                if type(self).armed:
                    type(self).armed = False
                    # Let the server finish and queue its 226 first.
                    time.sleep(0.2)
                    raise _Interrupt()
                callback(data)

            return super().retrbinary(cmd, interrupting, *args, **kwargs)

    monkeypatch.setattr(ftplib, "FTP", InterruptOnce)
    backend = _backend(url)
    p = _at(url, "small.bin", backend)
    with pytest.raises(_Interrupt):
        p.read_bytes()
    assert p.read_bytes() == b"x" * 100
    assert p.stat().st_size == 100
    _at(url, "c.txt", backend).write_bytes(b"cc")
    assert (root / "c.txt").read_bytes() == b"cc"


def test_ftp_write_after_idle_timeout_reconnects_and_lands(ftp_factory):
    url, handler, root = ftp_factory(timeout=1)
    p = _at(url, "late.txt")
    assert p.parent.is_dir()  # the session exists before the idle wait
    f = p.open("wb")
    f.write(b"important data")
    opened = time.monotonic()
    # The server drops the idle session; what the write must survive is that
    # drop, however long the machine takes to notice it. The client asks a
    # connection whether it is alive only once it has been idle for longer than
    # `IDLE_PROBE_SECONDS`, which is also the server's timeout here.
    idle_enough = ftp_mod.IDLE_PROBE_SECONDS + 0.25
    assert wait_until(
        lambda: handler.disconnections >= 1 and time.monotonic() - opened > idle_enough,
        timeout=60,
    )
    f.close()
    assert (root / "late.txt").read_bytes() == b"important data"
    assert handler.connections == 2


def test_ftp_without_mlsd_directories_stat_as_directories(ftp_factory):
    url, _handler, _root = ftp_factory(mlsd=False)
    base = _at(url, "/")
    d = base / "d"
    assert d.exists()
    assert d.is_dir()
    f = d / "f.txt"
    assert f.is_file()
    assert f.stat().st_size == 5
    assert not (base / "nope").exists()
    d.mkdir(parents=True, exist_ok=True)
    walked = [(p.name, sorted(ds), sorted(fs)) for p, ds, fs in d.walk()]
    assert walked == [("d", ["sub"], ["f.txt"]), ("sub", [], [])]
    with pytest.raises(NotADirectoryError):
        list(f.iterdir())
    with pytest.raises(FileNotFoundError):
        list((base / "nope").iterdir())


def test_ftp_refused_login_is_permission_error_not_missing(ftp_factory):
    url, handler, _root = ftp_factory()
    handler.auth_failed_timeout = 0.01  # pyftpdlib delays a failed login 3 s
    p = _at(url.replace(":12345@", ":wrong@"), "d/f.txt")
    with pytest.raises(PermissionError):
        p.stat()
    with pytest.raises(PermissionError):
        p.read_bytes()


def test_ftp_mlsd_mtime_is_utc(ftp_factory):
    url, _handler, root = ftp_factory()
    epoch = 1577880000  # 2020-01-01T12:00:00Z
    target = root / "t.txt"
    target.write_bytes(b"t")
    os.utime(target, (epoch, epoch))
    assert _at(url, "t.txt").stat().st_mtime == epoch


def test_ftp_refused_commands_map_to_permission_error(ftp_factory):
    url, _handler, root = ftp_factory(perm="elr")
    keep = _at(url, "d/f.txt")
    with pytest.raises(PermissionError):
        keep.unlink()
    with pytest.raises(PermissionError):
        keep.unlink(missing_ok=True)
    assert (root / "d" / "f.txt").exists()
    # A read-only login gets "550 Not enough privileges" for a missing file
    # too; it is still missing.
    missing = _at(url, "missing.txt")
    missing.unlink(missing_ok=True)
    with pytest.raises(FileNotFoundError):
        missing.unlink()
    with pytest.raises(PermissionError):
        _at(url, "new.txt").write_bytes(b"x")
    with pytest.raises(PermissionError):
        _at(url, "d").rmdir()
    with pytest.raises(PermissionError):
        _at(url, "d/f.txt").rename("g.txt")


def test_ftp_wrong_type_and_missing_targets_raise_like_pathlib(ftp_factory):
    url, _handler, root = ftp_factory()
    d = _at(url, "d")
    with pytest.raises(IsADirectoryError):
        d.read_bytes()
    with pytest.raises(IsADirectoryError):
        d.unlink()
    with pytest.raises(NotADirectoryError):
        _at(url, "d/f.txt").rmdir()
    with pytest.raises(OSError) as info:
        d.rmdir()
    assert info.value.errno == errno.ENOTEMPTY
    nope = _at(url, "nope")
    with pytest.raises(FileNotFoundError):
        nope.rmdir()
    with pytest.raises(FileNotFoundError):
        nope.rename("other")
    with pytest.raises(FileNotFoundError):
        nope.chmod(0o644)
    with pytest.raises(FileNotFoundError):
        list(nope.iterdir())
    with pytest.raises(NotADirectoryError):
        list(_at(url, "d/f.txt").iterdir())
    errors = []
    assert list(nope.walk(on_error=errors.append)) == []
    assert [type(e) for e in errors] == [FileNotFoundError]
    with pytest.raises(FileNotFoundError):
        _at(url, "nope/x.txt").write_bytes(b"x")
    _at(url, "a/b/c").mkdir(parents=True)
    assert (root / "a" / "b" / "c").is_dir()


def test_ftp_rplus_writes_land_and_read_stream_is_read_only(ftp_factory):
    url, _handler, root = ftp_factory()
    p = _at(url, "d/f.txt")
    with p.open("rb") as f:
        with pytest.raises(io.UnsupportedOperation):
            f.write(b"x")
    with p.open("r+b") as f:
        assert f.read() == b"hello"
        f.seek(0)
        f.write(b"J")
    assert (root / "d" / "f.txt").read_bytes() == b"Jello"


def test_ftp_failed_upload_closes_the_stream(ftp_factory):
    url, _handler, _root = ftp_factory(perm="elr")
    f = _at(url, "state.json").open("wb")
    f.write(b"v1")
    with pytest.raises(PermissionError):
        f.close()
    assert f.closed


def test_ftp_mlsd_perm_fact_is_a_known_mode(ftp_factory, tmp_path):
    url, _handler, _root = ftp_factory()
    st = _at(url, "d/f.txt").stat()
    assert st.mode_known
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) & 0o600 == 0o600
    assert _at(url, "d").stat().mode_known
    out = LocalPath(tmp_path / "out.txt")
    _at(url, "d/f.txt").copy(out)
    assert os.access(out, os.W_OK)
    _at(url, "d/f.txt").copy(out, overwrite=True)
    assert out.read_bytes() == b"hello"


def test_ftp_mlsd_unix_mode_fact_is_used():
    st = facts_to_filestat(
        {"type": "file", "size": "1", "unix.mode": "0755", "perm": "r"}
    )
    assert st.mode_known
    assert st.st_mode == stat.S_IFREG | 0o755
    st = facts_to_filestat({"type": "dir"})
    assert not st.mode_known


def _latin1_listing(handler):
    """Make the server send MLSD names as latin-1 bytes, which the client
    (utf-8) cannot decode."""
    from pyftpdlib.filesystems import AbstractedFS

    class LatinFS(AbstractedFS):
        def format_mlsx(self, *args, **kwargs):
            for line in super().format_mlsx(*args, **kwargs):
                yield line.replace("é".encode("utf-8"), b"\xe9")

    handler.abstracted_fs = LatinFS


def test_ftp_failed_listing_raises_oserror_and_the_next_request_reconnects(
    ftp_factory,
):
    url, handler, root = ftp_factory()
    (root / "legacy").mkdir()
    (root / "legacy" / "café.txt").write_bytes(b"x")
    (root / "ok.txt").write_bytes(b"content-f")
    _latin1_listing(handler)
    backend = _backend(url)
    ok = _at(url, "ok.txt", backend)
    assert ok.stat().st_size == 9
    assert handler.connections == 1

    with pytest.raises(OSError) as raised:
        list(_at(url, "legacy", backend).iterdir())
    assert raised.value.errno == errno.EILSEQ

    # The queued reply of the dead transfer was not left for the next command:
    # the client was dropped, and this request went out on a new connection.
    assert ok.stat().st_size == 9
    assert handler.connections == 2
    assert ok.exists()
    assert ok.is_file()
    assert [p.name for p in ok.parent.iterdir() if p.name == "ok.txt"] == ["ok.txt"]
    assert handler.connections == 2


def test_ftp_listing_refused_with_a_4xx_is_a_transient_oserror(ftp_factory):
    url, handler, _root = ftp_factory()

    def refuse(self, path=None):
        self.respond("425 Can't open data connection.")

    handler.ftp_MLSD = refuse
    handler.ftp_MLST = refuse  # stat() asks MLST where the server offers it
    backend = _backend(url)
    directory = _at(url, "d", backend)
    with pytest.raises(OSError) as raised:
        list(directory.iterdir())
    assert raised.value.errno == errno.EAGAIN
    with pytest.raises(OSError) as raised:
        (directory / "f.txt").stat()
    assert raised.value.errno == errno.EAGAIN
    assert not (directory / "f.txt").exists()
    # A complete reply leaves the control channel in step.
    assert handler.connections == 1


@pytest.mark.parametrize("mlsd", [True, False])
def test_ftp_an_empty_path_is_the_root_whatever_the_working_directory(
    ftp_factory, monkeypatch, mlsd
):
    url, handler, root = ftp_factory(mlsd=mlsd)
    sent = []
    base_cls = ftplib.FTP

    class Recorder(base_cls):
        def putline(self, line):
            sent.append(line)
            return super().putline(line)

    monkeypatch.setattr(ftplib, "FTP", Recorder)
    backend = _backend(url)
    bare = FTPPath("", backend=backend)
    # Moves the session's working directory (always without MLSD).
    assert (bare / "d" / "sub").is_dir()
    assert sorted(str(p) for p in bare.iterdir()) == ["d"]
    assert sorted(str(p) for p in FTPPath("/", backend=backend).iterdir()) == ["/d"]

    for operation in (
        bare.read_bytes,
        lambda: bare.write_bytes(b"x"),
        bare.unlink,
        bare.rmdir,
        bare.mkdir,
        lambda: list(bare.glob("*")),
        lambda: list(bare.walk()),
    ):
        try:
            operation()
        except Exception:
            pass
    takes_a_path = {
        "MLSD",
        "NLST",
        "CWD",
        "RETR",
        "STOR",
        "APPE",
        "DELE",
        "RMD",
        "MKD",
        "SIZE",
    }
    arguments = [
        (line.partition(" ")[0], line.partition(" ")[2].strip())
        for line in sent
        if line.partition(" ")[0] in takes_a_path
    ]
    assert arguments
    assert [verb for verb, argument in arguments if not argument] == []
    assert ("MLSD" if mlsd else "NLST", "/") in arguments
