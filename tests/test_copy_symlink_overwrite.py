"""`copy(follow_symlinks=False, overwrite=True)` of a link keeps the old target
until the new link exists: a link that cannot be made loses nothing."""

from __future__ import annotations

import errno
import os
import stat

import pytest

from pathapi import FileStat, LocalPath
from pathapi.mempath import MemPath, MemPathBackend
from pathapi.sync import _replace as swap


class _Link(str):
    """The text of a link, stored where `MemPath` keeps file content."""


class LinkMem(MemPath):
    """A `MemPath` that holds links but cannot rename."""

    refuse_new_links = False
    made: "list[str]" = []

    def _entry(self):
        parent, name = self._parent_container()
        return parent, name, parent.get(name) if name else None

    def stat(self, *, follow_symlinks=True):
        parent, name, entry = self._entry()
        if isinstance(entry, _Link) and not follow_symlinks:
            return FileStat(st_mode=stat.S_IFLNK | 0o777, st_size=len(entry))
        if isinstance(entry, _Link):
            return FileStat(st_size=len(entry))
        return MemPath.stat(self, follow_symlinks=follow_symlinks)

    def readlink(self):
        _, _, entry = self._entry()
        if not isinstance(entry, _Link):
            raise OSError(errno.EINVAL, "Invalid argument", str(self))
        return self.with_segments(str(entry))

    def _symlink_to(self, target, target_is_directory=False):
        parent, name, entry = self._entry()
        type(self).made.append(self.name)
        if type(self).refuse_new_links:
            raise PermissionError(errno.EACCES, "links refused", str(self))
        if entry is not None:
            raise FileExistsError(errno.EEXIST, "File exists", str(self))
        parent[name] = _Link(target.as_posix())


class RenamingLinkMem(LinkMem):
    """A `LinkMem` whose `rename()` replaces what is at the target."""

    def rename(self, target):
        if isinstance(target, str):
            target = self.with_segments(target)
        source_parent, source_name = self._parent_container()
        target_parent, target_name = target._parent_container()
        target_parent[target_name] = source_parent.pop(source_name)
        return target


@pytest.fixture(autouse=True)
def _reset():
    LinkMem.refuse_new_links = False
    LinkMem.made = []


def _symlink_or_skip(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlink unavailable: {error}")


def _names(root):
    return sorted(p.name for p in root.iterdir())


def _tree(cls):
    root = cls("/", backend=MemPathBackend())
    (root / "src")._symlink_to(root.with_segments("new-text"))
    (root / "dst")._symlink_to(root.with_segments("old-text"))
    LinkMem.made = []
    return root


class FailingLocal(LocalPath):
    """A `LocalPath` that refuses to make any link."""

    def _symlink_to(self, target, target_is_directory=False):
        raise PermissionError(errno.EACCES, "links refused", str(self))


def test_a_refused_link_keeps_the_existing_local_link(tmp_path):
    _symlink_or_skip(tmp_path / "src", "new-text")
    _symlink_or_skip(tmp_path / "dst", "old-text")

    with pytest.raises(PermissionError):
        LocalPath(tmp_path / "src").copy(
            FailingLocal(tmp_path / "dst"), overwrite=True, follow_symlinks=False
        )

    assert os.readlink(tmp_path / "dst") == "old-text"
    assert sorted(os.listdir(tmp_path)) == ["dst", "src"]


def test_a_refused_link_keeps_an_existing_local_file(tmp_path):
    _symlink_or_skip(tmp_path / "src", "new-text")
    (tmp_path / "dst").write_text("old content")

    with pytest.raises(PermissionError):
        LocalPath(tmp_path / "src").copy(
            FailingLocal(tmp_path / "dst"), overwrite=True, follow_symlinks=False
        )

    assert (tmp_path / "dst").read_text() == "old content"
    assert sorted(os.listdir(tmp_path)) == ["dst", "src"]


def test_a_local_link_replaces_an_existing_one(tmp_path):
    _symlink_or_skip(tmp_path / "src", "new-text")
    _symlink_or_skip(tmp_path / "dst", "old-text")

    LocalPath(tmp_path / "src").copy(
        LocalPath(tmp_path / "dst"), overwrite=True, follow_symlinks=False
    )

    assert os.readlink(tmp_path / "dst") == "new-text"
    assert sorted(os.listdir(tmp_path)) == ["dst", "src"]


def test_a_local_link_replaces_an_existing_file(tmp_path):
    _symlink_or_skip(tmp_path / "src", "new-text")
    (tmp_path / "dst").write_text("old content")

    LocalPath(tmp_path / "src").copy(
        LocalPath(tmp_path / "dst"), overwrite=True, follow_symlinks=False
    )

    assert os.readlink(tmp_path / "dst") == "new-text"
    assert sorted(os.listdir(tmp_path)) == ["dst", "src"]


def test_a_store_that_renames_keeps_its_link_when_the_new_one_is_refused():
    root = _tree(RenamingLinkMem)
    LinkMem.refuse_new_links = True

    with pytest.raises(PermissionError):
        (root / "src").copy(root / "dst", overwrite=True, follow_symlinks=False)

    assert str((root / "dst").readlink()) == "old-text"
    assert _names(root) == ["dst", "src"]


def test_a_store_that_renames_makes_the_link_beside_the_target_and_renames_it():
    root = _tree(RenamingLinkMem)

    (root / "src").copy(root / "dst", overwrite=True, follow_symlinks=False)

    assert str((root / "dst").readlink()) == "new-text"
    assert _names(root) == ["dst", "src"]
    assert len(LinkMem.made) == 1
    assert swap._is_leftover(LinkMem.made[0])


def test_a_store_that_cannot_rename_unlinks_first_and_makes_the_link():
    root = _tree(LinkMem)

    (root / "src").copy(root / "dst", overwrite=True, follow_symlinks=False)

    assert str((root / "dst").readlink()) == "new-text"
    assert LinkMem.made == ["dst"]
    assert _names(root) == ["dst", "src"]


def test_a_store_without_links_refuses_before_removing_the_target():
    root = _tree(LinkMem)
    plain = MemPath("/", backend=root.backend)

    with pytest.raises(NotImplementedError):
        (root / "src").copy(plain / "dst", overwrite=True, follow_symlinks=False)

    assert str((root / "dst").readlink()) == "old-text"
