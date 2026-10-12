"""`SftpPath` sets the access and modification time of a file through the
SFTP attributes request, on both client libraries, so `touch()` moves an
existing file's time and a sync can leave its copies with their source's time.
Every test reads the server's own file system afterwards."""

import os
import threading

import pytest

pytest.importorskip("asyncssh")

from pathlib_next import LocalPath  # noqa: E402
from pathlib_next.uri.schemes.sftp import SftpPath  # noqa: E402
from pathlib_next.uri.schemes.sftp import _asyncssh as backend_mod  # noqa: E402
from pathlib_next.sync import PathSyncer, SyncEvent  # noqa: E402

try:
    import paramiko
except ImportError:  # asyncssh-only install
    paramiko = None

#: 2001-09-09 01:46:40 UTC.
OLD = 1_000_000_000


def _make_backend(kind):
    if kind == "paramiko":
        if paramiko is None:
            pytest.skip("paramiko not installed")
        from pathlib_next.uri.schemes.sftp import SftpBackend

        return SftpBackend({"allow_agent": False, "look_for_keys": False})
    return backend_mod.AsyncsshSftpBackend()


def _close(backend, path):
    if paramiko is not None and not isinstance(
        backend, backend_mod.AsyncsshSftpBackend
    ):
        from pathlib_next.uri.schemes.sftp._paramiko import _CACHED_CLIENTS

        try:
            backend.client(path.source).sock.get_transport().close()
        except Exception:
            pass
        _CACHED_CLIENTS.invalidate(backend, path.source, threading.get_ident())
    else:
        backend.close()


@pytest.fixture(params=["paramiko", "asyncssh"])
def remote(request, sftp_server, fixture_tree):
    """(SftpPath at the server root, local path of that root)."""
    backend = _make_backend(request.param)
    root = SftpPath(sftp_server, backend=backend)
    try:
        yield root, fixture_tree
    finally:
        _close(backend, root)


def test_touch_moves_the_time_of_an_existing_remote_file(remote):
    root, local = remote
    os.utime(local / "a.txt", (OLD, OLD))

    (root / "a.txt").touch()

    stat = os.stat(local / "a.txt")
    assert stat.st_mtime > OLD + 1_000
    assert stat.st_atime > OLD + 1_000
    assert (local / "a.txt").read_text() == "a"


def test_utime_sets_whole_seconds_and_keeps_the_time_it_is_not_given(remote):
    root, local = remote
    os.utime(local / "a.txt", (OLD + 100, OLD))

    (root / "a.txt")._utime(mtime=OLD + 7.9)
    assert os.stat(local / "a.txt").st_mtime == OLD + 7
    assert os.stat(local / "a.txt").st_atime == OLD + 100

    (root / "a.txt")._utime(atime=OLD + 300)
    assert os.stat(local / "a.txt").st_atime == OLD + 300
    assert os.stat(local / "a.txt").st_mtime == OLD + 7


def test_utime_of_a_missing_file_raises_file_not_found(remote):
    root, _local = remote

    with pytest.raises(FileNotFoundError):
        (root / "absent.txt")._utime(mtime=OLD)


class Reads:
    """The files `SftpPath` opened for reading."""

    def __init__(self, monkeypatch):
        self.opened = []
        real = SftpPath._open

        def spy(path, mode="r", buffering=-1):
            if mode.startswith("r"):
                self.opened.append(path.name)
            return real(path, mode, buffering)

        monkeypatch.setattr(SftpPath, "_open", spy)
        # The loopback server counts as this machine: a quick check is for
        # pairs with a remote side.
        monkeypatch.setattr(SftpPath, "is_local", lambda path: False)


@pytest.fixture
def reads(monkeypatch):
    return Reads(monkeypatch)


def _source(tmp_path, mtime=OLD + 0.75):
    source = LocalPath(tmp_path / "source")
    (source / "d").mkdir(parents=True)
    for name in ("one.txt", "d/two.txt"):
        (source / name).write_text(name)
        os.utime(source / name, (mtime, mtime))
    return source


def test_sync_onto_sftp_leaves_the_source_time_and_the_next_run_reads_nothing(
    remote, tmp_path, reads
):
    root, local = remote
    source = _source(tmp_path)

    PathSyncer().sync(source, root / "dst")

    # SFTP stores whole seconds.
    assert os.stat(local / "dst" / "one.txt").st_mtime == OLD
    assert os.stat(local / "dst" / "d" / "two.txt").st_mtime == OLD
    reads.opened.clear()
    checksums = []

    def counting(entry):
        checksums.append(entry.path.name)
        return 0

    syncer = PathSyncer(counting)
    seen = []
    syncer._hook = lambda s, t, event, dry_run: seen.append(event)
    syncer.sync(source, root / "dst")

    assert SyncEvent.Copy not in seen
    assert checksums == []
    assert reads.opened == []


def test_sync_onto_sftp_without_preserve_mtime_reads_the_files_again(
    remote, tmp_path, reads
):
    root, local = remote
    source = _source(tmp_path)
    PathSyncer(preserve_mtime=False).sync(source, root / "dst")
    assert os.stat(local / "dst" / "one.txt").st_mtime > OLD + 1_000
    reads.opened.clear()

    PathSyncer(preserve_mtime=False).sync(source, root / "dst")

    assert sorted(reads.opened) == ["one.txt", "two.txt"]


def test_sync_from_sftp_gives_the_local_copy_the_server_time(remote, tmp_path_factory):
    root, local = remote
    os.utime(local / "sub" / "c.py", (OLD + 11, OLD + 11))
    copy = tmp_path_factory.mktemp("copy")

    PathSyncer().sync(root / "sub", LocalPath(copy / "sub"))

    assert os.stat(copy / "sub" / "c.py").st_mtime == OLD + 11
