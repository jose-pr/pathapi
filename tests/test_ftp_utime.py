"""`ftp:` sets a modification time with `MFMT` (draft-somers-ftp-mfxx), and only
on a server whose `FEAT` lists it. A scriptable loopback server logs every
command it receives (tests/ftp_loopback.py)."""

import pytest

pytest.importorskip("uritools")

from pathlib_next.ftp import _backend as ftp_mod
from pathlib_next.uri.schemes.ftp import FtpBackend, FtpPath

from ftp_loopback import LoopbackFtp

#: 2001-09-09 01:46:40 UTC.
OLD = 1_000_000_000
STAMP = "20010909014640"

FILE = "type=file;size=5;modify=20240101000000;"
FEATURES = ("MLST type*;size*;modify*;", "MLSD", "MFMT")


@pytest.fixture
def make():
    servers = []

    def start(**options):
        server = LoopbackFtp(**options)
        servers.append(server)
        backend = FtpBackend(timeout=5)

        def path(text):
            return FtpPath(f"{server.url}{text}", backend=backend)

        return server, path

    yield start
    for server in servers:
        for key in list(ftp_mod._CACHED_CLIENTS.cache):
            if key[1].port == server.port:
                ftp_mod._CACHED_CLIENTS.discard(*key)
        server.close()


def mfmt(reply="213 Modify=20010909014640; {arg}"):
    """An `MFMT` handler that answers `reply` and keeps what it was asked."""
    asked = []

    def handle(session, arg):
        asked.append(arg)
        _stamp, _, name = arg.partition(" ")
        session.send(reply.format(arg=name))
        return True

    handle.asked = asked
    return handle


def sent(server, verb):
    return [arg for _cid, command, arg in server.log if command == verb]


def test_utime_sends_mfmt_with_the_utc_time_when_feat_lists_it(make):
    handler = mfmt()
    server, path = make(
        files={"/d/f.txt": b"hello"},
        dirs={"/", "/d"},
        features=FEATURES,
        overrides={"MFMT": handler},
    )

    path("/d/f.txt")._utime(mtime=OLD + 0.9)

    assert handler.asked == [f"{STAMP} /d/f.txt"]


def test_feat_is_asked_once_per_connection(make):
    handler = mfmt()
    server, path = make(
        files={"/d/f.txt": b"hello", "/d/g.txt": b"hello"},
        dirs={"/", "/d"},
        features=FEATURES,
        overrides={"MFMT": handler},
    )

    path("/d/f.txt")._utime(mtime=OLD)
    path("/d/g.txt")._utime(mtime=OLD + 60)

    assert len(sent(server, "FEAT")) == 1
    assert len(handler.asked) == 2


def test_utime_without_a_modification_time_sends_nothing(make):
    server, path = make(
        files={"/d/f.txt": b"hello"}, dirs={"/", "/d"}, features=FEATURES
    )

    path("/d/f.txt")._utime(atime=OLD)

    assert sent(server, "MFMT") == [] and sent(server, "FEAT") == []


@pytest.mark.parametrize(
    "features",
    [("MLST type*;size*;modify*;", "MLSD"), None],
    ids=["feat-without-mfmt", "no-feat"],
)
def test_server_that_does_not_list_mfmt_is_not_asked(make, features):
    handler = mfmt()
    server, path = make(
        files={"/d/f.txt": b"hello"},
        dirs={"/", "/d"},
        features=features,
        overrides={"MFMT": handler},
    )

    with pytest.raises(NotImplementedError):
        path("/d/f.txt")._utime(mtime=OLD)

    assert sent(server, "MFMT") == []


def test_touch_of_a_file_on_a_server_without_mfmt_leaves_it_alone(make):
    server, path = make(
        files={"/d/f.txt": b"hello"},
        dirs={"/", "/d"},
        entries={"/d/f.txt": FILE},
        features=("MLST type*;size*;modify*;",),
    )

    path("/d/f.txt").touch()

    assert sent(server, "MFMT") == []
    assert sent(server, "STOR") == []
    assert server.files["/d/f.txt"] == b"hello"


def test_touch_of_an_existing_file_sends_mfmt_with_the_current_time(make):
    handler = mfmt()
    server, path = make(
        files={"/d/f.txt": b"hello"},
        dirs={"/", "/d"},
        entries={"/d/f.txt": FILE},
        features=FEATURES,
        overrides={"MFMT": handler},
    )

    path("/d/f.txt").touch()

    assert len(handler.asked) == 1
    stamp, _, name = handler.asked[0].partition(" ")
    assert name == "/d/f.txt" and len(stamp) == 14 and stamp > "20250101000000"
    assert server.files["/d/f.txt"] == b"hello"


def test_mfmt_the_server_does_not_implement_is_not_implemented(make):
    handler = mfmt(reply="502 MFMT not implemented")
    server, path = make(
        files={"/d/f.txt": b"hello"},
        dirs={"/", "/d"},
        features=FEATURES,
        overrides={"MFMT": handler},
    )

    with pytest.raises(NotImplementedError):
        path("/d/f.txt")._utime(mtime=OLD)


def test_mfmt_of_a_missing_file_is_file_not_found(make):
    handler = mfmt(reply="550 no such file")
    server, path = make(
        dirs={"/", "/d"}, entries={}, features=FEATURES, overrides={"MFMT": handler}
    )

    with pytest.raises(FileNotFoundError):
        path("/d/absent.txt")._utime(mtime=OLD)
