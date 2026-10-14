"""`move()` asks a backend about each root once and hands the answer on to
the `copy()` and the same-entry check it calls, instead of asking again."""

from __future__ import annotations

import collections

import pytest

from pathapi.path import _transfer
from pathapi.mempath import MemPath


class Counted(MemPath):
    """A `MemPath` that records, in order, every `stat()` and listing."""

    __slots__ = ()
    events: "list[tuple[str, str]]" = []

    def stat(self, *, follow_symlinks=True):
        type(self).events.append(("stat", self.as_posix()))
        return super().stat(follow_symlinks=follow_symlinks)

    def iterdir(self):
        type(self).events.append(("list", self.as_posix()))
        return super().iterdir()


def _tree():
    Counted.events = []
    src = Counted("/src")
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("b")
    dst = src.with_segments("/dst")
    Counted.events = []
    return src, dst


def _before_the_walk(root):
    """The stats of `root` recorded before the first listing."""
    seen = collections.Counter()
    for kind, name in Counted.events:
        if kind == "list":
            break
        seen[name] += 1
    return seen[root]


def test_a_directory_move_stats_each_root_once_before_it_walks():
    src, dst = _tree()

    src.move(dst)

    assert _before_the_walk("/src") == 1
    assert _before_the_walk("/dst") == 1
    assert (dst / "sub" / "b.txt").read_text() == "b"
    assert not src.exists()


def test_a_file_move_stats_each_root_once():
    src, dst = _tree()
    target = dst / "moved.txt"
    dst.mkdir()
    Counted.events = []

    (src / "a.txt").move(target)

    seen = collections.Counter(name for kind, name in Counted.events if kind == "stat")
    assert seen["/src/a.txt"] == 1
    assert seen["/dst/moved.txt"] == 1
    assert target.read_text() == "a"


def test_a_directory_moved_onto_an_existing_one_is_asked_about_again():
    src, dst = _tree()
    dst.mkdir()
    (dst / "old.txt").write_text("old")
    Counted.events = []

    src.move(dst, overwrite=True)

    assert sorted(p.name for p in dst.iterdir()) == ["a.txt", "sub"]
    assert (dst / "sub" / "b.txt").read_text() == "b"


def test_a_copy_of_the_same_pair_outside_a_move_takes_its_own_stats():
    src, dst = _tree()

    src.copy(dst, recursive=True)

    seen = collections.Counter(name for kind, name in Counted.events if kind == "stat")
    assert seen["/src"] == 1
    assert seen["/dst"] >= 1
    assert (dst / "sub" / "b.txt").read_text() == "b"


@pytest.mark.parametrize("overwrite", [False, True])
def test_nothing_handed_on_outlives_the_call(overwrite):
    src, dst = _tree()
    dst.mkdir()

    try:
        src.move(dst, overwrite=overwrite)
    except FileExistsError:
        pass

    assert _transfer._HANDED.get() is None


def test_a_refused_move_hands_nothing_on():
    src, _dst = _tree()

    with pytest.raises(OSError):
        src.move(src / "sub")

    assert _transfer._HANDED.get() is None


def test_the_stats_are_given_to_the_copy_of_exactly_that_pair():
    src, dst = _tree()
    other = src.with_segments("/other")

    with _transfer._handing(src, dst, src.stat(), True):
        other_pair = _transfer._take_handed(src, other)
        taken = _transfer._take_handed(src, dst)
        again = _transfer._take_handed(src, dst)

    assert other_pair is None
    assert taken is not None and taken.target_absent
    assert again is None


def test_a_class_with_its_own_samefile_is_asked_not_assumed():
    asked = []

    class Own(Counted):
        __slots__ = ()

        def _node_key(self):
            return None

        def samefile(self, other):
            asked.append(other.as_posix())
            return False

    Counted.events = []
    src = Own("/src")
    src.mkdir()
    (src / "a.txt").write_text("a")
    dst = src.with_segments("/dst")

    src.move(dst)

    assert asked == ["/dst"]
    assert (dst / "a.txt").read_text() == "a"


class _Identified:
    """A stat that reports `(st_dev, st_ino)` on top of another stat."""

    def __init__(self, stat, ino):
        self._stat = stat
        self.st_dev, self.st_ino = 7, ino

    def __getattr__(self, name):
        return getattr(self._stat, name)


class Inodes(Counted):
    """A `MemPath` that names no node and tells entries apart by identity."""

    __slots__ = ()

    def _node_key(self):
        return None

    def stat(self, *, follow_symlinks=True):
        names = MemPath._node_key(self)[1]
        return _Identified(
            super().stat(follow_symlinks=follow_symlinks), 1000 + hash(names) % 9973
        )


@pytest.mark.parametrize("overwrite", [False, True])
def test_a_second_spelling_of_one_entry_is_found_by_identity_and_kept(overwrite):
    root = Inodes("/")
    (root / "d").mkdir()
    (root / "a.txt").write_text("DATA")
    alias = root / "d" / ".." / "a.txt"
    Counted.events = []

    with pytest.raises(OSError):
        (root / "a.txt").move(alias, overwrite=overwrite)

    assert (root / "a.txt").read_text() == "DATA"
    seen = collections.Counter(name for kind, name in Counted.events if kind == "stat")
    assert seen["/a.txt"] <= 2
