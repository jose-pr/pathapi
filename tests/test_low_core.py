"""Regressions for low-severity soundness defects in the core `Path`
protocols, `MemPath`, `FileStat` and checksums."""

from __future__ import annotations

import errno
import hashlib
import inspect
import io
import os
import pathlib
import sys
import threading
import types

import pytest

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath, MemPathBackend
from pathlib_next.protocols import BinaryOpen
from pathlib_next import checksum
from pathlib_next import FileStat

# --- open(): handle cleanup, mode validation, line buffering ----------------


class _RecordingMemPath(MemPath):
    __slots__ = ()
    handles: list = []

    def _open(self, mode="r", buffering=-1):
        handle = super()._open(mode, buffering)
        _RecordingMemPath.handles.append(handle)
        return handle


@pytest.mark.parametrize(
    "kwargs, error",
    [({"encoding": "no-such-codec"}, LookupError), ({"newline": "bogus"}, ValueError)],
)
def test_open_closes_backend_handle_when_text_wrapper_fails(kwargs, error):
    path = _RecordingMemPath("/f.txt")
    path.write_bytes(b"x")
    _RecordingMemPath.handles.clear()
    with pytest.raises(error):
        path.open("r", **kwargs)
    assert len(_RecordingMemPath.handles) == 1
    assert _RecordingMemPath.handles[0].closed is True


@pytest.mark.parametrize(
    "mode, kwargs",
    [
        ("bt", {}),
        ("rw", {}),
        ("zz", {}),
        ("rr", {}),
        ("rb", {"encoding": "utf-8"}),
        ("rb", {"errors": "strict"}),
        ("rb", {"newline": ""}),
        ("r", {"buffering": 0}),
    ],
)
def test_open_rejects_invalid_modes_like_builtin_open(tmp_path, mode, kwargs):
    local = tmp_path / "f.txt"
    local.write_bytes(b"x")
    with pytest.raises(ValueError):
        open(local, mode, **kwargs)
    path = MemPath("/f.txt")
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        path.open(mode, **kwargs)


def test_open_text_buffering_one_is_line_buffered():
    path = MemPath("/lines.txt")
    with path.open("w", buffering=1) as handle:
        assert handle.line_buffering is True
        handle.write("a\n")
        # Line buffering pushes the line to the binary handle, whose flush
        # publishes it.
        assert path.read_text() == "a\n"
    with path.open("r") as handle:
        assert handle.line_buffering is False


# --- synthesized OSErrors carry errno and filename ---------------------------


def _raised(call):
    with pytest.raises(OSError) as info:
        call()
    return info.value


def test_mempath_errors_carry_errno_and_filename():
    root = MemPath("/")
    (root / "d").mkdir()
    (root / "f").write_bytes(b"x")
    cases = [
        (lambda: MemPath("/missing", backend=root.backend).rm(), errno.ENOENT),
        (lambda: (root / "missing").unlink(), errno.ENOENT),
        (lambda: (root / "missing").stat(), errno.ENOENT),
        (lambda: (root / "missing").open("rb"), errno.ENOENT),
        (lambda: (root / "missing").rmdir(), errno.ENOENT),
        (lambda: list((root / "missing").iterdir()), errno.ENOENT),
        (lambda: (root / "f").touch(exist_ok=False), errno.EEXIST),
        (lambda: (root / "d").mkdir(), errno.EEXIST),
        (lambda: (root / "f").open("xb"), errno.EEXIST),
        (lambda: (root / "d").unlink(), errno.EISDIR),
        (lambda: (root / "d").open("rb"), errno.EISDIR),
        (lambda: list((root / "f").iterdir()), errno.ENOTDIR),
        (lambda: (root / "f").rmdir(), errno.ENOTDIR),
    ]
    for call, code in cases:
        error = _raised(call)
        assert error.errno == code, (error, code)
        assert isinstance(error.filename, str) and error.filename, error


def test_generic_rm_missing_error_is_enoent_with_filename():
    error = _raised(lambda: MemPath("/missing").rm())
    assert isinstance(error, FileNotFoundError)
    assert (error.errno, error.filename) == (errno.ENOENT, "/missing")


def test_localpath_rm_missing_error_is_enoent(tmp_path):
    missing = LocalPath(tmp_path / "missing")
    error = _raised(missing.rm)
    assert isinstance(error, FileNotFoundError)
    assert error.errno == errno.ENOENT
    assert error.filename == str(missing)


# --- copy(progress=) on an empty file ---------------------------------------


def test_path_copy_progress_fires_once_for_empty_file():
    source = MemPath("/empty")
    source.write_bytes(b"")
    calls = []
    source.copy(
        source.with_segments("/copy"),
        progress=lambda path, copied, total: calls.append((path, copied, total)),
    )
    assert calls == [(source, 0, 0)]


def test_binaryopen_copy_progress_fires_once_for_empty_file():
    source = MemPath("/empty")
    source.write_bytes(b"")
    calls = []
    BinaryOpen.copy(
        source,
        source.with_segments("/copy"),
        progress=lambda copied, total: calls.append((copied, total)),
    )
    assert calls == [(0, 0)]


def test_copy_progress_for_nonempty_file_has_no_extra_zero_call():
    source = MemPath("/data")
    source.write_bytes(b"abc")
    calls = []
    source.copy(
        source.with_segments("/copy"),
        progress=lambda path, copied, total: calls.append((copied, total)),
    )
    assert calls == [(3, 3)]


# --- samefile() keeps the backend / host of a str argument ------------------


class _IdentityMemPath(MemPath):
    """A MemPath whose stat() reports a (st_dev, st_ino) identity."""

    __slots__ = ()

    def stat(self, *, follow_symlinks=True):
        parent, name = self._parent_container()
        if name not in parent:
            raise FileNotFoundError(errno.ENOENT, "missing", str(self))
        return types.SimpleNamespace(
            st_mode=0o100644, st_dev=id(self.backend), st_ino=id(parent[name])
        )


def test_samefile_str_resolves_on_the_same_backend():
    path = _IdentityMemPath("/a")
    path.write_bytes(b"1")
    path.with_segments("/b").write_bytes(b"2")
    # A fresh backend (the bare constructor) raised FileNotFoundError here.
    assert path.samefile("/a") is True
    assert path.samefile("/b") is False


# --- MemPath file handles behave like real files ----------------------------


def test_mempath_append_writes_at_end_after_seek():
    path = MemPath("/f")
    path.write_bytes(b"x")
    with path.open("ab") as handle:
        handle.seek(0)
        handle.write(b"Q")
        handle.writelines([b"R", b"S"])
    assert path.read_bytes() == b"xQRS"


def test_mempath_read_handle_is_not_writable():
    path = MemPath("/f")
    path.write_bytes(b"data")
    with path.open("rb") as handle:
        assert handle.writable() is False
        with pytest.raises(io.UnsupportedOperation):
            handle.write(b"zz")
        with pytest.raises(io.UnsupportedOperation):
            handle.truncate(0)
        assert handle.read() == b"data"
    assert path.read_bytes() == b"data"


def test_mempath_handle_double_close_is_a_no_op():
    path = MemPath("/f")
    handle = path.open("wb")
    handle.write(b"1")
    handle.close()
    handle.close()
    assert path.read_bytes() == b"1"


def test_mempath_flush_publishes_written_bytes():
    path = MemPath("/f")
    with path.open("w") as handle:
        handle.write("hello")
        handle.flush()
        assert path.read_text() == "hello"


def _race(action, trials=200):
    """Run `action(i)` on two threads at once per trial; return how many
    trials let both succeed."""
    both = 0
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for trial in range(trials):
            barrier = threading.Barrier(2)
            results = []

            def worker():
                barrier.wait()
                try:
                    action(trial)
                    results.append(True)
                except FileExistsError:
                    results.append(False)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert sorted(results) in ([False, True], [True, True])
            both += results == [True, True]
    finally:
        sys.setswitchinterval(interval)
    return both


def test_mempath_exclusive_create_is_atomic():
    backend = MemPathBackend()
    assert _race(lambda i: MemPath(f"/f{i}", backend=backend).open("xb").close()) == 0


def test_mempath_mkdir_is_atomic():
    backend = MemPathBackend()
    assert _race(lambda i: MemPath(f"/d{i}", backend=backend).mkdir()) == 0


def test_mempath_unlink_root_raises_even_with_missing_ok():
    with pytest.raises(IsADirectoryError):
        MemPath("/").unlink(missing_ok=True)
    with pytest.raises(IsADirectoryError):
        MemPath("/").unlink()


# --- FileStat.from_stat with None fields ------------------------------------


def test_filestat_from_stat_maps_none_fields_to_zero():
    foreign = types.SimpleNamespace(
        st_mode=None, st_size=None, st_uid=None, st_gid=None, st_mtime=None
    )
    stat = FileStat.from_stat(foreign)
    assert dict(stat.items()) == {field: 0 for field in FileStat._FIELDS}
    assert stat.is_dir() is False
    assert stat.mode_known is False
    assert repr(stat) == "<FileStat mode=0, size=0, mtime=0>"


# --- checksums do not claim a security use ----------------------------------


def test_checksums_work_when_security_md5_is_disabled(monkeypatch):
    real_new = hashlib.new

    def fips_new(name, *args, **kwargs):
        # A FIPS-mode OpenSSL rejects md5 unless usedforsecurity=False.
        if name.lower() == "md5" and kwargs.get("usedforsecurity", True):
            raise ValueError("[digital envelope routines] unsupported")
        return real_new(name, *args, **kwargs)

    def fips_md5(*args, **kwargs):
        return fips_new("md5", *args, **kwargs)

    monkeypatch.setattr(hashlib, "new", fips_new)
    monkeypatch.setattr(hashlib, "md5", fips_md5)
    path = MemPath("/f")
    path.write_bytes(b"abc")
    expected = real_new("md5", b"abc").hexdigest()
    assert checksum.md5(path) == expected
    assert checksum.stream(path, "md5") == expected
    assert checksum.sha256(path) == real_new("sha256", b"abc").hexdigest()


# --- is_dir()/is_file() accept follow_symlinks= (3.13 parity) ---------------


@pytest.mark.parametrize("name", ["is_dir", "is_file"])
@pytest.mark.parametrize("cls", [MemPath, LocalPath])
def test_is_dir_is_file_signature_has_follow_symlinks(cls, name):
    parameter = inspect.signature(getattr(cls, name)).parameters["follow_symlinks"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is True


def test_mempath_is_dir_is_file_follow_symlinks_keyword():
    root = MemPath("/")
    (root / "d").mkdir()
    (root / "f").write_bytes(b"")
    assert (root / "d").is_dir(follow_symlinks=False) is True
    assert (root / "f").is_file(follow_symlinks=False) is True
    assert (root / "f").is_dir(follow_symlinks=False) is False
    assert (root / "missing").is_file(follow_symlinks=False) is False


def test_mempath_is_dir_forwards_follow_symlinks_to_stat():
    calls = []

    class Spy(MemPath):
        __slots__ = ()

        def stat(self, *, follow_symlinks=True):
            calls.append(follow_symlinks)
            return FileStat(st_mode=0o120777)

    Spy("/x").is_dir(follow_symlinks=False)
    Spy("/x").is_file()
    assert calls == [False, True]


def test_localpath_is_dir_follow_symlinks_false_on_link(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "f").write_bytes(b"")
    link_dir = LocalPath(tmp_path / "link_dir")
    link_file = LocalPath(tmp_path / "link_file")
    try:
        link_dir.symlink_to(target, target_is_directory=True)
        link_file.symlink_to(target / "f")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"cannot create symlinks here: {error}")
    stdlib = pathlib.Path(link_dir)
    assert link_dir.is_dir() is True
    assert link_dir.is_dir(follow_symlinks=False) is False
    assert link_file.is_file() is True
    assert link_file.is_file(follow_symlinks=False) is False
    if sys.version_info >= (3, 13):
        assert stdlib.is_dir(follow_symlinks=False) is False
    assert LocalPath(tmp_path / "missing").is_dir(follow_symlinks=False) is False


# --- pure-path gaps: str / path, 3.14 trailing-dot suffixes -----------------


def test_str_rtruediv_mempath_keeps_backend_and_restarts_on_absolute():
    base = MemPath("b")
    joined = "a" / base
    assert isinstance(joined, MemPath)
    assert joined.as_posix() == "a/b"
    assert joined.backend is base.backend
    absolute = MemPath("/b")
    assert ("a" / absolute).as_posix() == "/b"
    assert ("a" / absolute).backend is absolute.backend
    with pytest.raises(TypeError):
        3 / base


_SUFFIX_NAMES = [
    "a",
    "a.b",
    "a.b.c",
    "a.",
    "a..",
    "..a",
    ".a.",
    "...",
    "a.b.",
    "a..b",
    ".a",
    ".a.b",
    "a.b..",
    "..a.b",
]


@pytest.mark.parametrize("name", _SUFFIX_NAMES)
def test_suffix_rules_follow_running_pathlib(name):
    expected = pathlib.PurePosixPath("x", name)
    path = MemPath("x", name)
    assert path.name == expected.name
    assert path.suffix == expected.suffix, path
    assert path.suffixes == expected.suffixes, path
    assert path.stem == expected.stem, path
    assert path.with_suffix(".t").name == expected.with_suffix(".t").name, path
