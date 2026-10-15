"""`copy(overwrite=True)` leaves the old target in place until the new content
is secured: a source that fails half way, or a missing one, loses nothing."""

import gc
import zipfile

import pytest

from pathapi import LocalPath
from pathapi.archive import ZipPath
from pathapi.mempath import MemPath
from pathapi.protocols.transfer import Move
from pathapi.providers import CommitOnClose


class _Boom(OSError):
    pass


class _Exploding:
    """A reader that hands out ten bytes and then fails."""

    def __init__(self, inner):
        self._inner = inner
        self._reads = 0

    def read(self, size=-1):
        self._reads += 1
        if self._reads > 1:
            raise _Boom("connection reset")
        return self._inner.read(10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.close()


@pytest.fixture
def failing_source(monkeypatch):
    """A 100-byte `MemPath` whose read fails after the first chunk."""
    src = MemPath("/") / "src.bin"
    src.write_bytes(b"x" * 100)
    original = MemPath.open

    def fake_open(self, mode="r", *args, **kwargs):
        handle = original(self, mode, *args, **kwargs)
        if self == src and "r" in mode:
            return _Exploding(handle)
        return handle

    monkeypatch.setattr(MemPath, "open", fake_open)
    return src


def _zip_member(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as archive:
        archive.writestr("old.txt", "OLD")
    return ZipPath("old.txt", archive=tmp_path / "a.zip")


def _local(tmp_path):
    target = LocalPath(tmp_path / "old.txt")
    target.write_bytes(b"OLD")
    return target


def _mem(tmp_path):
    target = MemPath("/") / "old.txt"
    target.write_bytes(b"OLD")
    return target


@pytest.mark.parametrize("make", [_local, _mem, _zip_member], ids=lambda f: f.__name__)
def test_a_source_failing_half_way_leaves_the_old_target(
    make, tmp_path, failing_source
):
    target = make(tmp_path)
    with pytest.raises(_Boom):
        failing_source.copy(target, overwrite=True)
    gc.collect()
    assert target.read_bytes() == b"OLD"


@pytest.mark.parametrize("make", [_local, _zip_member], ids=lambda f: f.__name__)
def test_a_failed_overwrite_leaves_no_temporary_sibling(make, tmp_path, failing_source):
    target = make(tmp_path)
    with pytest.raises(_Boom):
        failing_source.copy(target, overwrite=True)
    gc.collect()
    assert [child.name for child in target.parent.iterdir()] == ["old.txt"]


@pytest.mark.parametrize("make", [_local, _mem, _zip_member], ids=lambda f: f.__name__)
def test_a_successful_overwrite_replaces_the_content_and_leaves_one_file(
    make, tmp_path
):
    target = make(tmp_path)
    src = MemPath("/") / "new.bin"
    src.write_bytes(b"NEW" * 50)
    src.copy(target, overwrite=True)
    gc.collect()
    assert target.read_bytes() == b"NEW" * 50
    assert [child.name for child in target.parent.iterdir()] == ["old.txt"]


class _NoRename(MemPath):
    """An in-memory path that cannot rename and stores a file when its write
    stream is closed."""

    rename = Move.rename

    def open(self, mode="r", *args, **kwargs):
        if "w" in mode and "b" in mode:

            def commit(buffer):
                with MemPath.open(self, "wb") as stored:
                    stored.write(buffer.getvalue())

            return CommitOnClose(commit)
        return super().open(mode, *args, **kwargs)


def test_a_target_that_cannot_rename_keeps_its_content_when_the_source_fails(
    failing_source,
):
    target = _NoRename("/") / "old.txt"
    target.write_bytes(b"OLD")
    with pytest.raises(_Boom):
        failing_source.copy(target, overwrite=True)
    assert target.read_bytes() == b"OLD"


def test_a_target_that_cannot_rename_is_overwritten():
    target = _NoRename("/") / "old.txt"
    target.write_bytes(b"OLD")
    src = MemPath("/") / "new.bin"
    src.write_bytes(b"NEW")
    src.copy(target, overwrite=True)
    assert target.read_bytes() == b"NEW"
