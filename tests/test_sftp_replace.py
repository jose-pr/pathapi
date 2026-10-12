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
def two_files(sftp_root):
    (sftp_root / "a.txt").write_bytes(b"from a")
    (sftp_root / "b.txt").write_bytes(b"from b")


def _paths(sftp_loopback, backend):
    return (
        SftpPath(sftp_loopback.url("a.txt"), backend=backend),
        SftpPath(sftp_loopback.url("b.txt"), backend=backend),
    )


def _without_the_extension(monkeypatch, sftp_wire):
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

    def refuse(sftp_loopback, *args):
        raise asyncssh.SFTPOpUnsupported("unsupported")

    sftp_wire.before["posix_rename"] = refuse


def test_move_over_an_existing_file_is_one_posix_rename_and_no_remove(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    source, target = _paths(sftp_loopback, backend)
    source.exists()
    sftp_wire.reset()

    source.move(target, overwrite=True)

    assert sftp_wire.calls["posix_rename"] == 1
    assert sftp_wire.calls["remove"] == 0
    assert sftp_wire.calls["rename"] == 0
    assert not (sftp_root / "a.txt").exists()
    assert (sftp_root / "b.txt").read_bytes() == b"from a"


def test_replace_is_one_posix_rename_and_returns_the_target(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    source, target = _paths(sftp_loopback, backend)
    source.exists()
    sftp_wire.reset()

    result = source.replace(target)

    assert result == target
    assert sftp_wire.calls["posix_rename"] == 1
    assert sftp_wire.calls["remove"] == 0
    assert not (sftp_root / "a.txt").exists()
    assert (sftp_root / "b.txt").read_bytes() == b"from a"


def test_replace_takes_a_sibling_name_as_rename_does(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    source, _target = _paths(sftp_loopback, backend)
    sftp_wire.reset()

    result = source.replace("b.txt")

    assert result.path.endswith("/b.txt")
    assert sftp_wire.paths("posix_rename")[0].endswith(b"/a.txt")
    assert sftp_wire.calls["remove"] == 0
    assert (sftp_root / "b.txt").read_bytes() == b"from a"


def test_a_refused_posix_rename_leaves_both_files_as_they_were(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    def refuse(sftp_loopback, *args):
        raise asyncssh.SFTPPermissionDenied("denied")

    sftp_wire.before["posix_rename"] = refuse
    source, target = _paths(sftp_loopback, backend)

    with pytest.raises(PermissionError):
        source.move(target, overwrite=True)
    with pytest.raises(PermissionError):
        source.replace(target)

    assert sftp_wire.calls["remove"] == 0
    assert (sftp_root / "a.txt").read_bytes() == b"from a"
    assert (sftp_root / "b.txt").read_bytes() == b"from b"


def test_a_missing_source_is_not_found_and_the_target_stays(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    source = SftpPath(sftp_loopback.url("gone.txt"), backend=backend)
    target = SftpPath(sftp_loopback.url("b.txt"), backend=backend)

    with pytest.raises(FileNotFoundError):
        source.replace(target)

    assert sftp_wire.calls["remove"] == 0
    assert (sftp_root / "b.txt").read_bytes() == b"from b"


def test_without_the_extension_the_target_is_removed_and_the_file_renamed(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files, monkeypatch
):
    _without_the_extension(monkeypatch, sftp_wire)
    source, target = _paths(sftp_loopback, backend)
    sftp_wire.reset()

    source.move(target, overwrite=True)

    assert sftp_wire.calls["remove"] == 1
    assert not (sftp_root / "a.txt").exists()
    assert (sftp_root / "b.txt").read_bytes() == b"from a"


def test_replace_without_the_extension_does_the_same_and_learns_it(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files, monkeypatch
):
    _without_the_extension(monkeypatch, sftp_wire)
    source, target = _paths(sftp_loopback, backend)

    assert source.replace(target) == target

    assert sftp_wire.calls["remove"] == 1
    assert (sftp_root / "b.txt").read_bytes() == b"from a"
    asked = sftp_wire.calls["posix_rename"]
    (sftp_root / "c.txt").write_bytes(b"from c")
    SftpPath(sftp_loopback.url("c.txt"), backend=backend).replace(target)
    assert sftp_wire.calls["posix_rename"] == asked
    assert sftp_wire.calls["remove"] == 2
    assert (sftp_root / "b.txt").read_bytes() == b"from c"


def test_replace_of_a_directory_onto_one_that_holds_something_removes_nothing(
    sftp_loopback, sftp_root, sftp_wire, backend
):
    (sftp_root / "src").mkdir()
    (sftp_root / "src" / "s.txt").write_bytes(b"s")
    (sftp_root / "dst").mkdir()
    (sftp_root / "dst" / "d.txt").write_bytes(b"d")
    source = SftpPath(sftp_loopback.url("src"), backend=backend)
    target = SftpPath(sftp_loopback.url("dst"), backend=backend)

    with pytest.raises(OSError) as raised:
        source.replace(target)

    assert raised.value.errno == errno.ENOTEMPTY
    assert sftp_wire.calls["remove"] == 0 and sftp_wire.calls["rmdir"] == 0
    assert (sftp_root / "src" / "s.txt").read_bytes() == b"s"
    assert (sftp_root / "dst" / "d.txt").read_bytes() == b"d"


def test_replace_of_a_file_onto_a_directory_is_refused_and_removes_nothing(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    (sftp_root / "dir").mkdir()
    source = SftpPath(sftp_loopback.url("a.txt"), backend=backend)
    target = SftpPath(sftp_loopback.url("dir"), backend=backend)

    with pytest.raises(IsADirectoryError):
        source.replace(target)

    assert sftp_wire.calls["remove"] == 0 and sftp_wire.calls["rmdir"] == 0
    assert (sftp_root / "a.txt").read_bytes() == b"from a"
    assert (sftp_root / "dir").is_dir()


def test_replace_onto_another_server_copies_and_deletes(
    tmp_path, sftp_loopbacks, sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    other_root = tmp_path / "other"
    other_root.mkdir()
    (other_root / "b.txt").write_bytes(b"from the other b")
    other = sftp_loopbacks(other_root)
    source = SftpPath(sftp_loopback.url("a.txt"), backend=backend)
    target = SftpPath(other.url("b.txt"), backend=backend)

    source.replace(target)

    assert (other_root / "b.txt").read_bytes() == b"from a"
    assert not (sftp_root / "a.txt").exists()
    assert sftp_wire.calls["posix_rename"] == 0


def test_replace_of_a_path_onto_itself_keeps_the_file(
    sftp_loopback, sftp_root, sftp_wire, backend, two_files
):
    source, _target = _paths(sftp_loopback, backend)

    source.replace(source)

    assert sftp_wire.calls["remove"] == 0
    assert (sftp_root / "a.txt").read_bytes() == b"from a"
