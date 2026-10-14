"""A listing that knows an entry's type and not its size gives a type-only
stat (`FileStat.size_known` False). It answers `is_dir()`, `is_file()`,
`exists()`, `walk()`, `glob()` and `rm(recursive=True)`; whoever needs a size
or a time asks the path, so `stat()` of a listed child is exact and a sync
never takes the placeholder for a size."""

from __future__ import annotations

import os

import pytest

from pathapi.mempath import MemPath
from pathapi import FileStat
from pathapi.sync import PathAndStat, PathSyncer
from pathapi.sync._compare import _quick_check_in_sync

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


class TypeOnlyMem(MemPath):
    """A path whose listing reports a file's type and nothing else; a `stat()`
    of the file gives its real size and time and is counted."""

    __slots__ = ()

    stats: list = []
    #: The size and time a file's listing entry carries as placeholders.
    placeholder = (0, 0)

    def stat(self, *, follow_symlinks=True):
        real = super().stat(follow_symlinks=follow_symlinks)
        if not real.is_dir():
            TypeOnlyMem.stats.append(self.as_posix())
        return real

    def _scandir(self):
        for name, listed in super()._scandir():
            if listed is not None and not listed.is_dir():
                size, mtime = TypeOnlyMem.placeholder
                listed = FileStat(
                    st_size=size, st_mtime=mtime, is_dir=False, size_known=False
                )
            yield name, listed


def test_sync_asks_for_the_size_of_a_source_the_listing_gave_no_size():
    # The placeholder of the listing is the target's size and time; the file's
    # own are not. Taking the placeholder for a size would call the pair in
    # sync and leave the old content in the target.
    source = TypeOnlyMem("/s")
    source.mkdir()
    (source / "a.txt").write_bytes(b"hello")
    (source / "a.txt")._utime(mtime=2000)
    TypeOnlyMem.placeholder = (1, 1000)
    target = RemoteMem("/t")
    target.mkdir()
    (target / "a.txt").write_bytes(b"x")
    (target / "a.txt")._utime(mtime=1000)
    TypeOnlyMem.stats.clear()

    try:
        PathSyncer().sync(source, target)
    finally:
        TypeOnlyMem.placeholder = (0, 0)

    assert (target / "a.txt").read_bytes() == b"hello"
    assert "/s/a.txt" in TypeOnlyMem.stats


def test_sync_copies_every_file_of_a_type_only_listing():
    source = TypeOnlyMem("/s")
    (source / "sub" / "deep").mkdir(parents=True)
    (source / "a.txt").write_bytes(b"a")
    (source / "sub" / "deep" / "d.txt").write_bytes(b"dddd")
    target = MemPath("/t")
    target.mkdir()

    PathSyncer().sync(source, target)

    assert {p.name for p in target.rglob("*") if p.is_file()} == {"a.txt", "d.txt"}
    assert (target / "sub" / "deep" / "d.txt").read_bytes() == b"dddd"
