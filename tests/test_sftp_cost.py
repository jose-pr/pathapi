"""What the SFTP backends ask the server for: requests counted at the loopback
server, never timings.

A listing already describes every entry, so a walk that decides file or
directory from it, or a loop that tests each child of a directory, should not
ask again for what it was told.
"""

import os
import pathlib
import subprocess
import sys

import pytest

asyncssh = pytest.importorskip("asyncssh")

from pathlib_next.uri.schemes import sftp as sftp_pkg  # noqa: E402
from pathlib_next.uri.schemes.sftp import SftpPath  # noqa: E402
from pathlib_next.uri.schemes.sftp import _asyncssh as backend_mod  # noqa: E402

try:
    import paramiko
except ImportError:  # asyncssh-only install
    paramiko = None

SRC = str(pathlib.Path(__file__).resolve().parent.parent / "src")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture(params=["paramiko", "asyncssh"])
def kind(request):
    if request.param == "paramiko" and paramiko is None:
        pytest.skip("paramiko not installed")
    return request.param


@pytest.fixture
def backend(kind):
    if kind == "paramiko":
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
def asyncssh_backend():
    backend = backend_mod.AsyncsshSftpBackend({"known_hosts": None}, ssh_config=None)
    yield backend
    backend.close()


def make_tree(base, directories=8, files=16):
    for number in range(directories):
        folder = base / f"d{number}"
        folder.mkdir(parents=True)
        for index in range(files):
            (folder / f"f{index:02}.bin").write_bytes(b"x" * 64)


# --- a listing answers what a walk and a loop ask ----------------------------------


def test_native_rm_asks_about_no_entry_the_listing_described(
    sftp_loopback, sftp_root, sftp_wire, asyncssh_backend
):
    make_tree(sftp_root / "tree")
    tree = SftpPath(sftp_loopback.url("tree"), backend=asyncssh_backend)
    tree.exists()
    sftp_wire.reset()

    tree.rm(recursive=True)

    assert not (sftp_root / "tree").exists()
    # 128 files and 9 directories are removed; each of the 9 directories is
    # listed once and the root is looked at once. The server's own lstat of
    # "." and ".." for each listing (18) is part of its answer to the listing.
    assert sftp_wire.calls["remove"] == 128
    assert sftp_wire.calls["rmdir"] == 9
    assert sftp_wire.calls["scandir"] == 9
    assert sftp_wire.calls["lstat"] <= 19
    assert sftp_wire.total() <= 165


def test_a_loop_over_a_listing_stats_no_child_that_is_not_a_link(
    sftp_loopback, sftp_root, sftp_wire, backend
):
    (sftp_root / "dir").mkdir()
    for number in range(20):
        (sftp_root / "dir" / f"f{number:02}").write_bytes(b"x")
    (sftp_root / "dir" / "sub").mkdir()
    directory = SftpPath(sftp_loopback.url("dir"), backend=backend)
    directory.exists()
    sftp_wire.reset()

    kinds = [(child.name, child.is_dir()) for child in directory.iterdir()]

    assert sum(1 for _name, is_dir in kinds if is_dir) == 1
    assert len(kinds) == 21
    assert sftp_wire.calls["scandir"] == 1
    assert sftp_wire.calls["stat"] == 0


def test_a_listed_link_is_still_followed_by_a_stat_of_its_own(
    sftp_loopback, sftp_root, sftp_wire, backend
):
    (sftp_root / "dir" / "real").mkdir(parents=True)
    try:
        os.symlink(sftp_root / "dir" / "real", sftp_root / "dir" / "link", True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create a symbolic link")
    directory = SftpPath(sftp_loopback.url("dir"), backend=backend)
    directory.exists()
    sftp_wire.reset()

    by_name = {child.name: child for child in directory.iterdir()}
    sftp_wire.reset()
    assert by_name["real"].is_dir()
    assert sftp_wire.calls["stat"] == 0
    assert by_name["link"].is_dir()
    assert sftp_wire.calls["stat"] == 1
    assert by_name["link"].is_symlink()


def test_a_listed_entry_that_does_not_say_what_it_is_is_asked_about(
    sftp_loopback, sftp_root, sftp_wire, backend
):
    (sftp_root / "dir").mkdir()
    sftp_wire.listing = lambda path: [
        asyncssh.SFTPName(b"typeless", attrs=asyncssh.SFTPAttrs(size=3))
    ]
    directory = SftpPath(sftp_loopback.url("dir"), backend=backend)
    (child,) = [c for c in directory.iterdir() if c.name == "typeless"]
    sftp_wire.reset()

    assert not child.is_dir()

    assert sftp_wire.calls["stat"] == 1


def test_a_hint_is_used_once(sftp_loopback, sftp_root, sftp_wire, backend):
    (sftp_root / "dir").mkdir()
    (sftp_root / "dir" / "a").write_bytes(b"x")
    directory = SftpPath(sftp_loopback.url("dir"), backend=backend)
    (child,) = list(directory.iterdir())
    sftp_wire.reset()

    assert child.is_file()
    assert sftp_wire.count("stat", "lstat") == 0
    # What was learned from the listing is not trusted a second time.
    assert child.is_file()
    assert sftp_wire.count("stat", "lstat") == 1


# --- a backend that has no native tree operation never loads asyncssh -----------

_CHILD = """
import sys
from pathlib_next.uri.schemes.sftp import SftpBackend, SftpPath
import paramiko

url = sys.argv[1]
backend = SftpBackend(
    {"allow_agent": False, "look_for_keys": False},
    paramiko.AutoAddPolicy(),
    ssh_config=None,
    known_hosts=None,
)
assert not backend.supports_tree
root = SftpPath(url, backend=backend)
(root / "one.txt").copy(root / "two.txt")
(root / "tree" / "sub").mkdir(parents=True)
(root / "tree" / "sub" / "f.txt").write_bytes(b"x")
(root / "tree").copy(root / "tree2", recursive=True)
(root / "tree").rm(recursive=True)
print("asyncssh loaded:", "asyncssh" in sys.modules)
"""


@pytest.mark.skipif(paramiko is None, reason="paramiko not installed")
def test_copy_and_rm_on_the_paramiko_backend_do_not_import_asyncssh(
    sftp_loopback, sftp_root
):
    (sftp_root / "one.txt").write_bytes(b"1")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [SRC, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )

    finished = subprocess.run(
        [sys.executable, "-c", _CHILD, sftp_loopback.url("")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert finished.returncode == 0, finished.stderr
    assert "asyncssh loaded: False" in finished.stdout
    assert (sftp_root / "two.txt").read_bytes() == b"1"
    assert (sftp_root / "tree2" / "sub" / "f.txt").read_bytes() == b"x"
    assert not (sftp_root / "tree").exists()
