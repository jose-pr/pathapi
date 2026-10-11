"""`Path.info` answers the file-type questions of `pathlib.types.PathInfo` over
at most one following and one non-following `stat()`, and `_copy_from()` lets
Python 3.14's `pathlib.Path.copy()` target any `Path` of this library."""

from __future__ import annotations

import os
import pathlib
import stat as stat_module
import sys

import pytest

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath
from pathlib_next.path import _PathInfo
from pathlib_next.utils.stat import FileStat

OLD = 1_000_000_000


def tree():
    root = MemPath("/m")
    (root / "d").mkdir(parents=True)
    (root / "f.txt").write_text("file")
    (root / "d" / "g.txt").write_text("nested")
    return root


# --- info --------------------------------------------------------------------


def test_info_of_a_file_a_directory_and_a_missing_path():
    root = tree()

    f, d, gone = (root / "f.txt").info, (root / "d").info, (root / "gone").info

    assert (f.exists(), f.is_file(), f.is_dir(), f.is_symlink()) == (
        True,
        True,
        False,
        False,
    )
    assert (d.exists(), d.is_file(), d.is_dir(), d.is_symlink()) == (
        True,
        False,
        True,
        False,
    )
    assert (gone.exists(), gone.is_file(), gone.is_dir(), gone.is_symlink()) == (
        False,
        False,
        False,
        False,
    )


def test_info_methods_take_follow_symlinks_by_keyword():
    info = (tree() / "f.txt").info

    assert info.exists(follow_symlinks=False)
    assert info.is_file(follow_symlinks=False)
    assert not info.is_dir(follow_symlinks=False)
    with pytest.raises(TypeError):
        info.is_file(False)


class Counting(MemPath):
    """A `MemPath` that counts its stats, and can report a link."""

    __slots__ = ()

    calls: list = []
    links: set = set()

    def stat(self, *, follow_symlinks=True):
        Counting.calls.append((self.name, follow_symlinks))
        if self.name in Counting.links:
            if not follow_symlinks:
                return FileStat(st_mode=stat_module.S_IFLNK | 0o777)
            return FileStat(is_dir=True)
        return super().stat(follow_symlinks=follow_symlinks)


@pytest.fixture
def counting():
    Counting.calls = []
    Counting.links = set()
    root = Counting("/c")
    root.mkdir()
    (root / "f").write_text("x")
    Counting.calls.clear()
    return root


def test_info_asks_for_a_plain_file_once_whatever_it_is_asked(counting):
    info = (counting / "f").info

    assert info.is_symlink() is False
    assert info.is_file() and info.exists() and not info.is_dir()
    assert info.is_file(follow_symlinks=False)

    assert Counting.calls == [("f", False)]


def test_info_asks_a_link_twice_at_most_one_of_each_kind(counting):
    Counting.links.add("f")
    info = (counting / "f").info

    assert info.is_symlink() is True
    assert info.is_dir() is True
    assert info.is_dir(follow_symlinks=False) is False
    assert info.is_symlink() is True
    assert info.exists() and info.exists(follow_symlinks=False)

    assert sorted(Counting.calls) == [("f", False), ("f", True)]


def test_info_following_first_then_not_asks_twice_for_a_plain_file(counting):
    info = (counting / "f").info

    assert info.is_file()
    assert info.is_symlink() is False
    assert info.is_file()

    assert sorted(Counting.calls) == [("f", False), ("f", True)]


def test_a_missing_path_is_asked_once_per_kind(counting):
    info = (counting / "gone").info

    assert not info.exists() and not info.is_file() and not info.is_symlink()
    assert not info.exists(follow_symlinks=False)

    assert sorted(Counting.calls) == [("gone", False), ("gone", True)]


def test_the_answers_live_on_the_info_object_only():
    root = tree()
    path = root / "f.txt"
    first = path.info
    assert first.is_file()

    path.unlink()

    assert first.is_file()  # remembered by this object
    assert not path.info.exists()  # a new info asks again
    assert not path.info.is_file()


def test_info_swallows_what_a_stat_swallows():
    class Failing(MemPath):
        __slots__ = ()

        def stat(self, *, follow_symlinks=True):
            raise PermissionError(13, "denied")

    info = Failing("/x").info

    assert info.exists() is False and info.is_dir() is False


def test_info_is_a_pathlib_pathinfo_on_python_3_14():
    types = pytest.importorskip("pathlib.types", reason="pathlib.types is 3.14+")
    protocol = getattr(types, "PathInfo", None)
    if protocol is None:
        pytest.skip("this pathlib.types has no PathInfo")

    assert isinstance((tree() / "f.txt").info, protocol)


def test_localpath_info_is_pathlibs_own_on_3_14_and_ours_before(tmp_path):
    info = LocalPath(tmp_path).info

    assert info.is_dir() and info.exists()
    assert isinstance(info, _PathInfo) is (sys.version_info < (3, 14))


# --- _copy_from: what a path is given by pathlib 3.14's Path.copy() ------------------


def test_copy_from_copies_a_tree_between_paths_of_this_library():
    target = MemPath("/t")

    target._copy_from(tree())

    assert (target / "f.txt").read_text() == "file"
    assert (target / "d" / "g.txt").read_text() == "nested"


def test_copy_from_overwrites_a_file_and_refuses_an_existing_directory():
    target = MemPath("/t")
    target.mkdir()
    (target / "f.txt").write_text("old and longer")

    (target / "f.txt")._copy_from(tree() / "f.txt")
    assert (target / "f.txt").read_text() == "file"

    with pytest.raises(FileExistsError):
        target._copy_from(tree())


def test_copy_from_with_metadata_gives_the_copy_the_source_time():
    source = tree()
    (source / "f.txt")._utime(mtime=OLD)
    target = MemPath("/t")

    target._copy_from(source, preserve_metadata=True)

    assert (target / "f.txt").stat().st_mtime == OLD
    other = MemPath("/u")
    other._copy_from(source)
    assert (other / "f.txt").stat().st_mtime > OLD + 1_000


STDLIB_HOOKS = (
    "needs Python 3.14, whose pathlib.Path.copy() calls target._copy_from() "
    "after pathlib._os.ensure_distinct_paths(); this pathlib no longer has them"
)


def _stdlib_copy_hooks_exist():
    if sys.version_info < (3, 14):
        return False
    try:
        from pathlib._os import ensure_distinct_paths  # noqa: F401
    except ImportError:
        return False
    return hasattr(pathlib.Path, "_copy_from")


stdlib_copy = pytest.mark.skipif(not _stdlib_copy_hooks_exist(), reason=STDLIB_HOOKS)


@stdlib_copy
def test_stdlib_path_copy_reaches_a_mempath_with_the_bytes(tmp_path):
    source = tmp_path / "f.bin"
    source.write_bytes(b"\x00\x01payload\xff" * 5000)
    target = MemPath("/x")

    returned = source.copy(target)

    assert target.read_bytes() == source.read_bytes()
    assert returned == target


@stdlib_copy
def test_stdlib_path_copy_of_a_tree_reaches_a_mempath(tmp_path):
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "a.txt").write_text("a")
    (tmp_path / "src" / "sub" / "b.txt").write_text("b")
    target = MemPath("/dst")

    pathlib.Path(tmp_path / "src").copy(target)

    assert (target / "a.txt").read_text() == "a"
    assert (target / "sub" / "b.txt").read_text() == "b"


@stdlib_copy
def test_stdlib_path_copy_overwrites_a_file_and_refuses_a_directory(tmp_path):
    source = tmp_path / "f.txt"
    source.write_text("new")
    target = MemPath("/x")
    target.write_text("old")
    source.copy(target)
    assert target.read_text() == "new"

    (tmp_path / "d").mkdir()
    target_dir = MemPath("/y")
    target_dir.mkdir()
    with pytest.raises(FileExistsError):
        (tmp_path / "d").copy(target_dir)


@stdlib_copy
def test_stdlib_path_copy_with_metadata_sets_the_modification_time(tmp_path):
    source = tmp_path / "f.txt"
    source.write_text("x")
    os.utime(source, (OLD, OLD))
    target = MemPath("/x")

    source.copy(target, preserve_metadata=True)

    assert target.stat().st_mtime == OLD
    other = MemPath("/z")
    source.copy(other)
    assert other.stat().st_mtime > OLD + 1_000


@stdlib_copy
def test_stdlib_path_copy_without_following_links_makes_a_link_where_it_can(tmp_path):
    link = tmp_path / "link"
    try:
        os.symlink("elsewhere", link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    target = LocalPath(tmp_path / "copy")

    link.copy(target, follow_symlinks=False)

    assert os.readlink(target) == "elsewhere"


@stdlib_copy
def test_stdlib_path_copy_reaches_a_file_uri(tmp_path):
    pytest.importorskip("uritools")
    from pathlib_next.uri.schemes.file import FileUri

    source = tmp_path / "f.txt"
    source.write_text("through a URI")
    target = FileUri((tmp_path / "out.txt").as_uri())

    source.copy(target)

    assert (tmp_path / "out.txt").read_text() == "through a URI"
