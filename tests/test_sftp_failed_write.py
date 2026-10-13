"""An `sftp:` file is written through as the stream is used: the server opens
(and truncates) the file when `open()` returns, and a `with` block that raised
leaves what was written, as a local file does. Nothing is held back for `close()`
to commit, so `discard()` has no meaning for it."""

import pytest

asyncssh = pytest.importorskip("asyncssh")

from pathlib_next.uri.schemes import sftp as sftp_pkg  # noqa: E402
from pathlib_next.uri.schemes.sftp import SftpPath  # noqa: E402
from pathlib_next.uri.schemes.sftp import _asyncssh as backend_mod  # noqa: E402

try:
    import paramiko
except ImportError:  # asyncssh-only install
    paramiko = None


class Abort(Exception):
    pass


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture(params=["paramiko", "asyncssh"])
def backend(request):
    if request.param == "paramiko":
        if paramiko is None:
            pytest.skip("paramiko not installed")
        backend = sftp_pkg.SftpBackend(
            {"allow_agent": False, "look_for_keys": False},
            paramiko.AutoAddPolicy(),
            ssh_config=None,
            known_hosts=None,
        )
    else:
        backend = backend_mod.AsyncsshSftpBackend(
            {"known_hosts": None}, ssh_config=None
        )
    yield backend
    backend.close()


def test_the_file_is_truncated_when_the_stream_is_opened(
    sftp_loopback, sftp_root, backend
):
    (sftp_root / "f.bin").write_bytes(b"OLD CONTENT")
    stream = SftpPath(sftp_loopback.url("f.bin"), backend=backend).open("wb")
    try:
        assert (sftp_root / "f.bin").read_bytes() == b""
    finally:
        stream.close()


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_a_block_that_raised_leaves_what_was_written(
    sftp_loopback, sftp_root, backend, mode, data
):
    (sftp_root / "f.bin").write_bytes(b"OLD CONTENT")
    path = SftpPath(sftp_loopback.url("f.bin"), backend=backend)
    with pytest.raises(Abort):
        with path.open(mode) as f:
            f.write(data)
            raise Abort
    assert (sftp_root / "f.bin").read_bytes() == b"part"
