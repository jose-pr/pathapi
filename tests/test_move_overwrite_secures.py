"""`move(overwrite=True)` keeps the old target until what replaces it is in
place: a rename or a copy that fails leaves both the target and the source."""

from __future__ import annotations

import errno
import os

import pytest

from pathapi import LocalPath
from pathapi.mempath import MemPath, MemPathBackend
from pathapi.sync import _replace as swap


class _Boom(OSError):
    pass


class RenamingMem(MemPath):
    """A `MemPath` that renames, as the backends with a native `rename()` do.
    `fail_for` names sources whose rename raises; `refuses_existing` makes a
    rename onto an existing entry fail the way a store without POSIX rename
    semantics does."""

    fail_for: "set[str]" = set()
    refuses_existing = False
    merges_directories = False
    calls: "list[tuple[str, str]]" = []

    def rename(self, target):
        if isinstance(target, str):
            target = self.with_segments(target)
        type(self).calls.append((self.name, target.name))
        if self.name in type(self).fail_for:
            raise PermissionError(errno.EACCES, "rename refused", str(self))
        source_parent, source_name = self._parent_container()
        target_parent, target_name = target._parent_container()
        if source_name not in source_parent:
            raise FileNotFoundError(errno.ENOENT, "No such file", str(self))
        existing = target_parent.get(target_name)
        if existing is not None:
            if type(self).refuses_existing:
                raise FileExistsError(errno.EEXIST, "File exists", str(target))
            if isinstance(existing, dict) and type(self).merges_directories:
                # An object store: a directory is a key prefix, so the
                # renamed keys join the ones already there.
                existing.update(source_parent.pop(source_name))
                return target
            if isinstance(existing, dict) and existing:
                raise OSError(errno.ENOTEMPTY, "Directory not empty", str(target))
        target_parent[target_name] = source_parent.pop(source_name)
        return target


@pytest.fixture(autouse=True)
def _reset():
    RenamingMem.fail_for = set()
    RenamingMem.refuses_existing = False
    RenamingMem.merges_directories = False
    RenamingMem.calls = []


def _tree(cls=RenamingMem):
    root = cls("/", backend=MemPathBackend())
    return root


def _names(root):
    return sorted(p.name for p in root.iterdir())


def _leftovers(root):
    return [name for name in _names(root) if swap._is_leftover(name)]


# --- a backend with rename ------------------------------------------------


def test_a_failed_rename_keeps_the_existing_file_target_and_the_source():
    root = _tree()
    (root / "src.txt").write_text("new")
    (root / "dst.txt").write_text("old")
    RenamingMem.fail_for = {"src.txt"}

    with pytest.raises(PermissionError):
        (root / "src.txt").move(root / "dst.txt", overwrite=True)

    assert (root / "dst.txt").read_text() == "old"
    assert (root / "src.txt").read_text() == "new"
    assert _leftovers(root) == []


def test_a_rename_that_replaces_is_one_rename_and_nothing_else():
    root = _tree()
    (root / "src.txt").write_text("new")
    (root / "dst.txt").write_text("old")

    (root / "src.txt").move(root / "dst.txt", overwrite=True)

    assert RenamingMem.calls == [("src.txt", "dst.txt")]
    assert (root / "dst.txt").read_text() == "new"
    assert _names(root) == ["dst.txt"]


def test_a_store_that_refuses_to_rename_over_a_file_still_moves_it():
    root = _tree()
    (root / "src.txt").write_text("new")
    (root / "dst.txt").write_text("old")
    RenamingMem.refuses_existing = True

    (root / "src.txt").move(root / "dst.txt", overwrite=True)

    assert (root / "dst.txt").read_text() == "new"
    assert _names(root) == ["dst.txt"]


def test_the_displaced_file_comes_back_when_the_second_rename_fails():
    root = _tree()
    (root / "src.txt").write_text("new")
    (root / "dst.txt").write_text("old")
    RenamingMem.refuses_existing = True
    RenamingMem.fail_for = {"src.txt"}

    with pytest.raises(OSError):
        (root / "src.txt").move(root / "dst.txt", overwrite=True)

    assert (root / "dst.txt").read_text() == "old"
    assert (root / "src.txt").read_text() == "new"
    assert _leftovers(root) == []


def test_a_failed_rename_keeps_an_existing_directory_target():
    root = _tree()
    (root / "src").mkdir()
    (root / "src" / "n.txt").write_text("new")
    (root / "dst").mkdir()
    (root / "dst" / "o.txt").write_text("old")
    RenamingMem.fail_for = {"src"}

    with pytest.raises(OSError):
        (root / "src").move(root / "dst", overwrite=True)

    assert (root / "dst" / "o.txt").read_text() == "old"
    assert (root / "src" / "n.txt").read_text() == "new"
    assert _leftovers(root) == []


def test_a_directory_moved_over_a_directory_replaces_it_whole():
    root = _tree()
    (root / "src").mkdir()
    (root / "src" / "n.txt").write_text("new")
    (root / "dst").mkdir()
    (root / "dst" / "o.txt").write_text("old")

    (root / "src").move(root / "dst", overwrite=True)

    assert _names(root) == ["dst"]
    assert _names(root / "dst") == ["n.txt"]


def test_a_directory_is_replaced_whole_where_rename_would_merge_into_it():
    root = _tree()
    (root / "src").mkdir()
    (root / "src" / "n.txt").write_text("new")
    (root / "dst").mkdir()
    (root / "dst" / "o.txt").write_text("old")
    RenamingMem.merges_directories = True

    (root / "src").move(root / "dst", overwrite=True)

    assert _names(root / "dst") == ["n.txt"]


# --- a backend without rename (copy and delete) ---------------------------


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


def _fail_reading(monkeypatch, name):
    original = MemPath.open

    def fake_open(self, mode="r", *args, **kwargs):
        handle = original(self, mode, *args, **kwargs)
        if self.name == name and "r" in mode:
            return _Exploding(handle)
        return handle

    monkeypatch.setattr(MemPath, "open", fake_open)


def test_a_failed_copy_keeps_the_existing_file_target_and_the_source(monkeypatch):
    root = MemPath("/", backend=MemPathBackend())
    (root / "src.bin").write_bytes(b"x" * 100)
    (root / "dst.bin").write_bytes(b"old")
    _fail_reading(monkeypatch, "src.bin")

    with pytest.raises(_Boom):
        (root / "src.bin").move(root / "dst.bin", overwrite=True)

    monkeypatch.undo()
    assert (root / "dst.bin").read_bytes() == b"old"
    assert (root / "src.bin").read_bytes() == b"x" * 100
    assert _leftovers(root) == []


def test_a_failed_copy_keeps_the_existing_directory_target(monkeypatch):
    root = MemPath("/", backend=MemPathBackend())
    (root / "src").mkdir()
    (root / "src" / "n.bin").write_bytes(b"x" * 100)
    (root / "dst").mkdir()
    (root / "dst" / "o.txt").write_text("old")
    _fail_reading(monkeypatch, "n.bin")

    with pytest.raises(_Boom):
        (root / "src").move(root / "dst", overwrite=True)

    monkeypatch.undo()
    assert (root / "dst" / "o.txt").read_text() == "old"
    assert (root / "src" / "n.bin").read_bytes() == b"x" * 100
    assert _leftovers(root) == []


def test_a_directory_copied_and_deleted_replaces_the_target_whole():
    root = MemPath("/", backend=MemPathBackend())
    (root / "src").mkdir()
    (root / "src" / "n.txt").write_text("new")
    (root / "dst").mkdir()
    (root / "dst" / "o.txt").write_text("old")

    (root / "src").move(root / "dst", overwrite=True)

    assert _names(root) == ["dst"]
    assert _names(root / "dst") == ["n.txt"]
    assert (root / "dst" / "n.txt").read_text() == "new"


def test_a_directory_copied_and_deleted_over_a_directory_that_renames():
    # The source cannot rename, the target's class can: the new tree is
    # built beside the target and swapped in.
    other = RenamingMem("/", backend=MemPathBackend())
    (other / "dst").mkdir()
    (other / "dst" / "o.txt").write_text("old")
    root = MemPath("/", backend=MemPathBackend())
    (root / "src").mkdir()
    (root / "src" / "n.txt").write_text("new")

    (root / "src").move(other / "dst", overwrite=True)

    assert _names(other) == ["dst"]
    assert _names(other / "dst") == ["n.txt"]
    assert not (root / "src").exists()


def test_a_failed_copy_to_a_target_that_renames_keeps_the_target(monkeypatch):
    other = RenamingMem("/", backend=MemPathBackend())
    (other / "dst").mkdir()
    (other / "dst" / "o.txt").write_text("old")
    root = MemPath("/", backend=MemPathBackend())
    (root / "src").mkdir()
    (root / "src" / "n.bin").write_bytes(b"x" * 100)
    _fail_reading(monkeypatch, "n.bin")

    with pytest.raises(_Boom):
        (root / "src").move(other / "dst", overwrite=True)

    monkeypatch.undo()
    assert _names(other) == ["dst"]
    assert (other / "dst" / "o.txt").read_text() == "old"
    assert (root / "src" / "n.bin").exists()


# --- the local filesystem ---------------------------------------------------


def _refuse_renaming(monkeypatch, sample, only=None):
    """Make the renames of the local class refuse, for every source named
    `only` (every source when None), whichever interpreter's `pathlib`
    reaches the operating system through."""
    cls = type(sample)
    real_rename, real_replace = cls.rename, cls.replace

    def guard(real):
        def wrapped(self, target):
            if only is None or self.name == only:
                raise PermissionError(errno.EACCES, "refused", str(self))
            return real(self, target)

        return wrapped

    monkeypatch.setattr(cls, "rename", guard(real_rename))
    monkeypatch.setattr(cls, "replace", guard(real_replace))


def test_a_failed_rename_keeps_a_local_file_target(tmp_path, monkeypatch):
    (tmp_path / "src.txt").write_text("new")
    (tmp_path / "dst.txt").write_text("old")
    src, dst = LocalPath(tmp_path / "src.txt"), LocalPath(tmp_path / "dst.txt")
    _refuse_renaming(monkeypatch, src)

    with pytest.raises(PermissionError):
        src.move(dst, overwrite=True)

    assert (tmp_path / "dst.txt").read_text() == "old"
    assert (tmp_path / "src.txt").read_text() == "new"


def test_a_failed_rename_keeps_a_local_directory_target(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "n.txt").write_text("new")
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "o.txt").write_text("old")
    src, dst = LocalPath(tmp_path / "src"), LocalPath(tmp_path / "dst")
    _refuse_renaming(monkeypatch, src, only="src")

    with pytest.raises(PermissionError):
        src.move(dst, overwrite=True)

    monkeypatch.undo()
    assert (tmp_path / "dst" / "o.txt").read_text() == "old"
    assert (tmp_path / "src" / "n.txt").read_text() == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dst", "src"]


def test_a_local_directory_moved_over_a_directory_replaces_it_whole(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "n.txt").write_text("new")
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "o.txt").write_text("old")

    LocalPath(tmp_path / "src").move(LocalPath(tmp_path / "dst"), overwrite=True)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["dst"]
    assert sorted(p.name for p in (tmp_path / "dst").iterdir()) == ["n.txt"]
