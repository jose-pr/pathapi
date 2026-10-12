"""A listing that knows an entry's type and not its size gives a type-only
stat (`FileStat.size_known` False). It answers `is_dir()`, `is_file()`,
`exists()`, `walk()`, `glob()` and `rm(recursive=True)`; whoever needs a size
or a time asks the path, so `stat()` of a listed child is exact and a sync
never takes the placeholder for a size."""

from __future__ import annotations

import io
import os

import pytest

pytest.importorskip("uritools")

from pathlib_next.mempath import MemPath
from pathlib_next.uri import UriPath
from pathlib_next import FileStat
from pathlib_next.sync import PathAndStat, PathSyncer
from pathlib_next.sync import _quick_check_in_sync

# --- FileStat ------------------------------------------------------------


def test_a_stat_knows_its_size_unless_it_says_otherwise():
    assert FileStat(st_size=3).size_known is True
    assert FileStat(is_dir=False, size_known=False).size_known is False
    assert FileStat(is_dir=False, size_known=False).is_file()


def test_from_stat_keeps_the_flag_and_defaults_it_for_a_foreign_stat(tmp_path):
    placeholder = FileStat(is_dir=False, size_known=False)
    assert FileStat.from_stat(placeholder) is placeholder
    (tmp_path / "f").write_text("abc")
    assert FileStat.from_stat(os.stat(tmp_path / "f")).size_known is True

    class Foreign:
        st_mode = 0o100644
        size_known = False

    assert FileStat.from_stat(Foreign()).size_known is False


# --- a path class whose listing knows the type only -----------------------------


class TypeOnlyG4(UriPath):
    """A flat store whose listing reports a file's type and nothing else; a
    `stat()` of the file gives its real size and time and is counted."""

    __SCHEMES = ("typeonlyg4",)
    __slots__ = ()

    FILES: dict = {}
    DIRS: set = set()
    stats: list = []
    listings: list = []
    #: The size and time a file's listing entry carries as placeholders.
    placeholder = (0, 0)
    #: The time a `stat()` of any file reports.
    mtime = 1000

    @classmethod
    def reset(cls, files):
        cls.FILES = dict(files)
        cls.DIRS = {"/"} | {
            "/" + "/".join(path.split("/")[1:depth])
            for path in cls.FILES
            for depth in range(2, path.count("/") + 1)
        }
        cls.stats = []
        cls.listings = []
        cls.placeholder = (0, 0)
        cls.mtime = 1000

    def _key(self):
        return "/" + "/".join(s for s in self.path.split("/") if s)

    def stat(self, *, follow_symlinks=True):
        hint = self._pop_stat_hint()
        if hint is not None:
            return hint
        key = self._key()
        TypeOnlyG4.stats.append(key)
        if key in self.DIRS:
            return FileStat(is_dir=True)
        if key in self.FILES:
            return FileStat(
                st_size=len(self.FILES[key]), st_mtime=self.mtime, is_dir=False
            )
        raise FileNotFoundError(key)

    def _scandir(self):
        key = self._key()
        TypeOnlyG4.listings.append(key)
        prefix = key.rstrip("/") + "/"
        names = {}
        for directory in self.DIRS:
            if directory != key and directory.startswith(prefix):
                rest = directory[len(prefix) :]
                if "/" not in rest:
                    names[rest] = FileStat(is_dir=True)
        for path in self.FILES:
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                size, mtime = self.placeholder
                names[path[len(prefix) :]] = FileStat(
                    st_size=size, st_mtime=mtime, is_dir=False, size_known=False
                )
        yield from names.items()

    def _open(self, mode="r", buffering=-1):
        if mode != "r":
            raise NotImplementedError(mode)
        key = self._key()
        if key not in self.FILES:
            raise FileNotFoundError(key)
        return io.BytesIO(self.FILES[key])

    def unlink(self, missing_ok=False):
        if self.FILES.pop(self._key(), None) is None and not missing_ok:
            raise FileNotFoundError(self._key())

    def rmdir(self):
        self.DIRS.discard(self._key())


TREE = {
    "/a.txt": b"a",
    "/sub/b.txt": b"bb",
    "/sub/deep/c.txt": b"ccc",
    "/sub/deep/d.txt": b"dddd",
}


@pytest.fixture
def store():
    TypeOnlyG4.reset(TREE)
    yield TypeOnlyG4("typeonlyg4://127.0.0.1/")
    TypeOnlyG4.reset({})


def test_walk_lists_each_directory_once_and_stats_no_file(store):
    names = {
        (str(top.path).rstrip("/") or "/"): (sorted(dirs), sorted(files))
        for top, dirs, files in store.walk()
    }

    assert names == {
        "/": (["sub"], ["a.txt"]),
        "/sub": (["deep"], ["b.txt"]),
        "/sub/deep": ([], ["c.txt", "d.txt"]),
    }
    assert TypeOnlyG4.stats == []
    assert sorted(TypeOnlyG4.listings) == ["/", "/sub", "/sub/deep"]


def test_glob_answers_the_type_without_a_stat_per_file(store):
    found = sorted(p.name for p in store.glob("**/*.txt"))

    assert found == ["a.txt", "b.txt", "c.txt", "d.txt"]
    # The directories are stat-ed (the root of the call); no file is.
    assert set(TypeOnlyG4.stats) <= {"/"}


@pytest.mark.parametrize(
    "question, answer",
    [
        ("is_file", True),
        ("is_dir", False),
        ("exists", True),
        ("is_symlink", False),
    ],
)
def test_a_listed_child_answers_a_type_question_without_a_request(
    store, question, answer
):
    child = next(p for p in store.iterdir() if p.name == "a.txt")

    assert getattr(child, question)() is answer
    assert TypeOnlyG4.stats == []


def test_stat_of_a_listed_child_is_the_real_one_and_asks_once(store):
    child = next(p for p in store.iterdir() if p.name == "a.txt")

    stat = child.stat()

    assert (stat.st_size, stat.st_mtime, stat.size_known) == (1, 1000, True)
    assert TypeOnlyG4.stats == ["/a.txt"]


def test_the_type_hint_is_single_use_like_any_other(store):
    child = next(p for p in store.iterdir() if p.name == "a.txt")
    child.is_file()
    TypeOnlyG4.FILES.pop("/a.txt")

    assert not child.exists()
    assert TypeOnlyG4.stats == ["/a.txt"]


def test_a_stat_after_a_type_question_still_asks(store):
    child = next(p for p in store.iterdir() if p.name == "a.txt")
    assert child.is_file()

    assert child.stat().st_size == 1
    assert TypeOnlyG4.stats == ["/a.txt"]


def test_rm_recursive_removes_the_tree_from_the_listing_alone(store):
    (store / "sub").rm(recursive=True)

    assert sorted(TypeOnlyG4.FILES) == ["/a.txt"]
    assert "/sub/deep/c.txt" not in TypeOnlyG4.FILES
    # The root of the call is stat-ed once; no entry below it is.
    assert TypeOnlyG4.stats == ["/sub"]


class UnsizedMem(MemPath):
    """A path whose `stat()` itself reports a type and no size."""

    __slots__ = ()

    def stat(self, *, follow_symlinks=True):
        real = super().stat(follow_symlinks=follow_symlinks)
        if real.is_dir():
            return real
        return FileStat(is_dir=False, size_known=False)


def test_copy_reports_an_unknown_total_for_a_stat_without_a_size():
    source = UnsizedMem("/s")
    source.mkdir()
    (source / "f").write_bytes(b"abcdef")
    target = MemPath("/t")
    target.mkdir()
    totals = []

    (source / "f").copy(
        target / "f", progress=lambda path, copied, total: totals.append(total)
    )

    assert (target / "f").read_bytes() == b"abcdef"
    assert totals and set(totals) == {None}


# --- a sync never takes a placeholder for a size ---------------------------------


class RemoteMem(MemPath):
    __slots__ = ()

    def is_local(self):
        return False


def entry(stat):
    return PathAndStat.from_stat(MemPath("/x"), stat)


def test_quick_check_never_matches_a_stat_whose_size_is_unknown():
    unknown = FileStat(st_size=3, st_mtime=1000, is_dir=False, size_known=False)
    known = FileStat(st_size=3, st_mtime=1000, is_dir=False)

    assert not _quick_check_in_sync(entry(unknown), entry(known))
    assert not _quick_check_in_sync(entry(known), entry(unknown))
    assert not _quick_check_in_sync(entry(unknown), entry(unknown))
    assert _quick_check_in_sync(entry(known), entry(known))


def test_sync_asks_for_the_size_of_a_source_the_listing_gave_no_size(store):
    # The placeholder of the listing is the target's size and time; the file's
    # own are not. Taking the placeholder for a size would call the pair in
    # sync and leave the old content in the target.
    TypeOnlyG4.FILES["/a.txt"] = b"hello"
    TypeOnlyG4.placeholder = (1, 1000)
    TypeOnlyG4.mtime = 2000
    target = RemoteMem("/t")
    target.mkdir()
    (target / "a.txt").write_bytes(b"x")
    (target / "a.txt")._utime(mtime=1000)
    TypeOnlyG4.stats.clear()

    PathSyncer().sync(store, target)

    assert (target / "a.txt").read_bytes() == b"hello"
    assert "/a.txt" in TypeOnlyG4.stats


def test_sync_copies_every_file_of_a_type_only_listing(store):
    mem = MemPath("/t")
    mem.mkdir()

    PathSyncer().sync(store, mem)

    assert {p.name for p in mem.rglob("*") if p.is_file()} == {
        "a.txt",
        "b.txt",
        "c.txt",
        "d.txt",
    }
    assert (mem / "sub" / "deep" / "d.txt").read_bytes() == b"dddd"
