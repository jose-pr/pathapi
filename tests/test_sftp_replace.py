"""`SftpPath.replace()` and `move(overwrite=True)` onto an existing file are one
`posix-rename@openssh.com` request that replaces it, counted at an in-process
loopback server: the target is not removed first, so a request that fails
leaves both files as they were. A server without the extension gets the
remove-then-rename it always got."""

import errno

import pytest

asyncssh = pytest.importorskip("asyncssh")

from pathlib_next.uri.schemes import sftp as sftp_pkg  # noqa: E402
from pathlib_next.uri.schemes.sftp import SftpPath  # noqa: E402
from pathlib_next.uri.schemes.sftp import _asyncssh as backend_mod  # noqa: E402

from sftp_loopback import Loopback, Wire  # noqa: E402

try:
    import paramiko
except ImportError:  # asyncssh-only install
    paramiko = None


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


@pytest.fixture
def wire():
    return Wire()


@pytest.fixture
def server(root, wire):
    loopback = Loopback(root, wire).start()
    yield loopback
    loopback.stop()


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


@pytest.fixture
def two_files(root):
    (root / "a.txt").write_bytes(b"from a")
    (root / "b.txt").write_bytes(b"from b")


def _paths(server, backend):
    return (
        SftpPath(server.url("a.txt"), backend=backend),
        SftpPath(server.url("b.txt"), backend=backend),
    )


def _without_the_extension(monkeypatch, wire):
    """A server that does not offer `posix-rename@openssh.com` (the asyncssh
    client sees it is missing) and answers the request with "unsupported" if
    it is sent anyway (paramiko sends it regardless)."""
    from asyncssh import sftp as asyncssh_sftp

    handler = asyncssh_sftp.SFTPServerHandler
    monkeypatch.setattr(
        handler,
        "_extensions",
        [e for e in handler._extensions if e[0] != b"posix-rename@openssh.com"],
    )

    def refuse(server, *args):
        raise asyncssh.SFTPOpUnsupported("unsupported")

    wire.before["posix_rename"] = refuse


def test_move_over_an_existing_file_is_one_posix_rename_and_no_remove(
    server, root, wire, backend, two_files
):
    source, target = _paths(server, backend)
    source.exists()
    wire.reset()

    source.move(target, overwrite=True)

    assert wire.calls["posix_rename"] == 1
    assert wire.calls["remove"] == 0
    assert wire.calls["rename"] == 0
    assert not (root / "a.txt").exists()
    assert (root / "b.txt").read_bytes() == b"from a"


def test_replace_is_one_posix_rename_and_returns_the_target(
    server, root, wire, backend, two_files
):
    source, target = _paths(server, backend)
    source.exists()
    wire.reset()

    result = source.replace(target)

    assert result == target
    assert wire.calls["posix_rename"] == 1
    assert wire.calls["remove"] == 0
    assert not (root / "a.txt").exists()
    assert (root / "b.txt").read_bytes() == b"from a"


def test_replace_takes_a_sibling_name_as_rename_does(
    server, root, wire, backend, two_files
):
    source, _target = _paths(server, backend)
    wire.reset()

    result = source.replace("b.txt")

    assert result.path.endswith("/b.txt")
    assert wire.paths("posix_rename")[0].endswith(b"/a.txt")
    assert wire.calls["remove"] == 0
    assert (root / "b.txt").read_bytes() == b"from a"


def test_a_refused_posix_rename_leaves_both_files_as_they_were(
    server, root, wire, backend, two_files
):
    def refuse(server, *args):
        raise asyncssh.SFTPPermissionDenied("denied")

    wire.before["posix_rename"] = refuse
    source, target = _paths(server, backend)

    with pytest.raises(PermissionError):
        source.move(target, overwrite=True)
    with pytest.raises(PermissionError):
        source.replace(target)

    assert wire.calls["remove"] == 0
    assert (root / "a.txt").read_bytes() == b"from a"
    assert (root / "b.txt").read_bytes() == b"from b"


def test_a_missing_source_is_not_found_and_the_target_stays(
    server, root, wire, backend, two_files
):
    source = SftpPath(server.url("gone.txt"), backend=backend)
    target = SftpPath(server.url("b.txt"), backend=backend)

    with pytest.raises(FileNotFoundError):
        source.replace(target)

    assert wire.calls["remove"] == 0
    assert (root / "b.txt").read_bytes() == b"from b"


def test_without_the_extension_the_target_is_removed_and_the_file_renamed(
    server, root, wire, backend, two_files, monkeypatch
):
    _without_the_extension(monkeypatch, wire)
    source, target = _paths(server, backend)
    wire.reset()

    source.move(target, overwrite=True)

    assert wire.calls["remove"] == 1
    assert not (root / "a.txt").exists()
    assert (root / "b.txt").read_bytes() == b"from a"


def test_replace_without_the_extension_does_the_same_and_learns_it(
    server, root, wire, backend, two_files, monkeypatch
):
    _without_the_extension(monkeypatch, wire)
    source, target = _paths(server, backend)

    assert source.replace(target) == target

    assert wire.calls["remove"] == 1
    assert (root / "b.txt").read_bytes() == b"from a"
    asked = wire.calls["posix_rename"]
    (root / "c.txt").write_bytes(b"from c")
    SftpPath(server.url("c.txt"), backend=backend).replace(target)
    assert wire.calls["posix_rename"] == asked
    assert wire.calls["remove"] == 2
    assert (root / "b.txt").read_bytes() == b"from c"


def test_replace_of_a_directory_onto_one_that_holds_something_removes_nothing(
    server, root, wire, backend
):
    (root / "src").mkdir()
    (root / "src" / "s.txt").write_bytes(b"s")
    (root / "dst").mkdir()
    (root / "dst" / "d.txt").write_bytes(b"d")
    source = SftpPath(server.url("src"), backend=backend)
    target = SftpPath(server.url("dst"), backend=backend)

    with pytest.raises(OSError) as raised:
        source.replace(target)

    assert raised.value.errno == errno.ENOTEMPTY
    assert wire.calls["remove"] == 0 and wire.calls["rmdir"] == 0
    assert (root / "src" / "s.txt").read_bytes() == b"s"
    assert (root / "dst" / "d.txt").read_bytes() == b"d"


def test_replace_of_a_file_onto_a_directory_is_refused_and_removes_nothing(
    server, root, wire, backend, two_files
):
    (root / "dir").mkdir()
    source = SftpPath(server.url("a.txt"), backend=backend)
    target = SftpPath(server.url("dir"), backend=backend)

    with pytest.raises(IsADirectoryError):
        source.replace(target)

    assert wire.calls["remove"] == 0 and wire.calls["rmdir"] == 0
    assert (root / "a.txt").read_bytes() == b"from a"
    assert (root / "dir").is_dir()


def test_replace_onto_another_server_copies_and_deletes(
    tmp_path, server, root, wire, backend, two_files
):
    other_root = tmp_path / "other"
    other_root.mkdir()
    (other_root / "b.txt").write_bytes(b"from the other b")
    other = Loopback(other_root, Wire()).start()
    try:
        source = SftpPath(server.url("a.txt"), backend=backend)
        target = SftpPath(other.url("b.txt"), backend=backend)

        source.replace(target)

        assert (other_root / "b.txt").read_bytes() == b"from a"
        assert not (root / "a.txt").exists()
        assert wire.calls["posix_rename"] == 0
    finally:
        other.stop()


def test_replace_of_a_path_onto_itself_keeps_the_file(
    server, root, wire, backend, two_files
):
    source, _target = _paths(server, backend)

    source.replace(source)

    assert wire.calls["remove"] == 0
    assert (root / "a.txt").read_bytes() == b"from a"
