"""A sync gives each copy the modification time of its source, so the next
run's quick check matches; `touch()` of an existing file moves its time; and
two times are compared at the resolution of the coarser side.
"""

from __future__ import annotations

import os
import time

import pytest

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath
from pathlib_next.utils.stat import FileStat
from pathlib_next.utils.sync import PathAndStat, PathSyncer, SyncEvent
from pathlib_next.utils.sync import _quick_check_in_sync, _same_mtime

#: 2001-09-09 01:46:40 UTC, a time no copy or touch made today can produce.
OLD = 1_000_000_000


class RemoteMem(MemPath):
    """A `MemPath` that stands in for a path on another host, and counts the
    files opened for reading."""

    __slots__ = ()

    reads: list = []

    def is_local(self):
        return False

    def _open(self, mode="r", buffering=-1):
        if mode == "r":
            RemoteMem.reads.append(str(self))
        return super()._open(mode, buffering)


@pytest.fixture(autouse=True)
def _no_reads():
    RemoteMem.reads.clear()


def local_tree(tmp_path, mtime=OLD + 0.25):
    source = LocalPath(tmp_path / "src")
    (source / "d").mkdir(parents=True)
    for name in ("a.txt", "d/b.txt"):
        path = source / name
        path.write_text(name)
        os.utime(path, (mtime, mtime))
    return source


def events_of(syncer):
    seen = []
    syncer._hook = lambda source, target, event, dry_run: seen.append(event)
    return seen


def test_sync_gives_a_copy_the_modification_time_of_its_source(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()

    PathSyncer().sync(source, target)

    assert (target / "a.txt").stat().st_mtime == pytest.approx(OLD + 0.25)
    assert (target / "d" / "b.txt").stat().st_mtime == pytest.approx(OLD + 0.25)


def test_second_sync_of_an_unchanged_tree_reads_no_file(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()
    PathSyncer().sync(source, target)
    checksums = []

    def counting(entry):
        checksums.append(entry.path)
        return 0

    syncer = PathSyncer(counting)
    seen = events_of(syncer)
    syncer.sync(source, target)

    assert SyncEvent.Copy not in seen
    assert checksums == []
    assert RemoteMem.reads == []


def test_sync_reads_the_files_again_without_preserve_mtime(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()
    PathSyncer(preserve_mtime=False).sync(source, target)
    assert (target / "a.txt").stat().st_mtime > OLD + 1_000

    PathSyncer(preserve_mtime=False).sync(source, target)

    assert sorted(RemoteMem.reads) == ["/t/a.txt", "/t/d/b.txt"]


def test_changed_source_with_the_same_size_is_copied_again(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()
    PathSyncer().sync(source, target)
    (source / "a.txt").write_text("a.txt".upper())
    os.utime(source / "a.txt", (OLD + 99, OLD + 99))

    PathSyncer().sync(source, target)

    assert (target / "a.txt").read_text() == "A.TXT"
    assert (target / "a.txt").stat().st_mtime == pytest.approx(OLD + 99)


def test_local_to_local_sync_keeps_the_source_time_and_still_checksums(tmp_path):
    source = local_tree(tmp_path)
    target = LocalPath(tmp_path / "dst")
    PathSyncer().sync(source, target)
    assert (target / "a.txt").stat().st_mtime == pytest.approx(OLD + 0.25, abs=1e-3)
    checksums = []

    def counting(entry):
        checksums.append(entry.path.name)
        return 0

    PathSyncer(counting).sync(source, target)

    assert "a.txt" in checksums


class CannotSetTime(RemoteMem):
    __slots__ = ()

    def _utime(self, *, atime=None, mtime=None):
        raise NotImplementedError("no times here")


class RefusesTime(RemoteMem):
    __slots__ = ()

    def _utime(self, *, atime=None, mtime=None):
        raise PermissionError(13, "the server refused", str(self))


@pytest.mark.parametrize("target_class", [CannotSetTime, RefusesTime])
def test_a_time_that_cannot_be_set_is_not_an_error_of_the_sync(tmp_path, target_class):
    source = local_tree(tmp_path)
    target = target_class("/t")
    target.mkdir()
    syncer = PathSyncer()
    seen = events_of(syncer)

    syncer.sync(source, target)

    assert (target / "a.txt").read_text() == "a.txt"
    assert SyncEvent.Error not in seen
    # The copy keeps the time it was written at.
    assert (target / "a.txt").stat().st_mtime > OLD + 1_000


def test_a_source_with_no_time_leaves_the_time_of_the_copy(tmp_path):
    source = MemPath("/s")
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "a.txt")._utime(mtime=0)
    assert (source / "a.txt").stat().st_mtime == 0
    target = RemoteMem("/t")
    target.mkdir()

    PathSyncer().sync(source, target)

    assert (target / "a.txt").stat().st_mtime > OLD + 1_000


def test_dry_run_sets_no_time(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()

    PathSyncer().sync(source, target, dry_run=True)

    assert list(target.iterdir()) == []


def test_sync_replacing_a_file_keeps_the_source_time(tmp_path):
    source = local_tree(tmp_path)
    target = RemoteMem("/t")
    target.mkdir()
    (target / "a.txt").write_text("stale and longer")

    PathSyncer().sync(source, target)

    assert (target / "a.txt").read_text() == "a.txt"
    assert (target / "a.txt").stat().st_mtime == pytest.approx(OLD + 0.25)


# touch() of an existing file


def test_touch_moves_the_time_of_an_existing_memfile():
    root = MemPath("/m")
    root.mkdir()
    (root / "f").write_text("kept")
    (root / "f")._utime(mtime=OLD)

    (root / "f").touch()

    assert (root / "f").stat().st_mtime > OLD + 1_000
    assert (root / "f").read_text() == "kept"


def test_touch_with_exist_ok_false_leaves_the_time_and_raises():
    root = MemPath("/m")
    root.mkdir()
    (root / "f").write_text("kept")
    (root / "f")._utime(mtime=OLD)

    with pytest.raises(FileExistsError):
        (root / "f").touch(exist_ok=False)

    assert (root / "f").stat().st_mtime == OLD


def test_touch_of_a_backend_that_cannot_set_a_time_leaves_the_file_alone():
    root = CannotSetTime("/m")
    root.mkdir()
    (root / "f").write_text("kept")
    before = (root / "f").stat().st_mtime

    (root / "f").touch()

    assert (root / "f").stat().st_mtime == before
    assert (root / "f").read_text() == "kept"


def test_touch_of_a_file_that_refuses_the_time_raises_that_error():
    root = RefusesTime("/m")
    root.mkdir()
    (root / "f").write_text("kept")

    with pytest.raises(PermissionError):
        (root / "f").touch()


def test_utime_of_a_memfile_sets_only_the_time_it_is_given():
    root = MemPath("/m")
    root.mkdir()
    (root / "f").write_text("x")

    (root / "f")._utime(mtime=OLD + 0.5)
    (root / "f")._utime(atime=5)

    assert (root / "f").stat().st_mtime == OLD + 0.5


def test_utime_of_a_directory_or_a_missing_file_of_a_memfile_tree():
    root = MemPath("/m")
    root.mkdir()
    (root / "d").mkdir()

    with pytest.raises(NotImplementedError):
        (root / "d")._utime(mtime=OLD)
    with pytest.raises(FileNotFoundError):
        (root / "absent")._utime(mtime=OLD)


def test_localpath_utime_sets_the_time_it_is_given_and_keeps_the_other(tmp_path):
    f = LocalPath(tmp_path / "f")
    f.write_text("x")
    os.utime(f, (OLD, OLD))

    f._utime(mtime=OLD + 0.5)
    assert f.stat().st_mtime == pytest.approx(OLD + 0.5)
    assert f.stat().st_atime == pytest.approx(OLD)
    f._utime(atime=OLD + 7)
    assert f.stat().st_atime == pytest.approx(OLD + 7)
    assert f.stat().st_mtime == pytest.approx(OLD + 0.5)


# Times are compared at the resolution of the coarser side.


@pytest.mark.parametrize(
    "first, second, same",
    [
        (OLD, OLD, True),
        # The same second, one side in whole seconds.
        (OLD + 0.25, OLD, True),
        (OLD, OLD + 0.999, True),
        (float(OLD), OLD + 0.5, True),
        # One second apart is a different time, whichever side has fractions.
        (OLD, OLD + 1, False),
        (OLD + 0.5, OLD + 1, False),
        (OLD + 1.5, OLD, False),
        (OLD + 0.5, OLD + 1.5, False),
        # Both with fractions: the fractions decide.
        (OLD + 0.25, OLD + 0.5, False),
        (OLD + 0.25, OLD + 0.25, True),
        (OLD + 0.25, OLD + 0.25 + 2e-7, True),
    ],
)
def test_times_match_at_the_resolution_of_the_coarser_side(first, second, same):
    assert _same_mtime(first, second) is same
    assert _same_mtime(second, first) is same


def entry(size, mtime, **kwargs):
    return PathAndStat.from_stat(
        MemPath("/x"), FileStat(st_size=size, st_mtime=mtime, **kwargs)
    )


def test_quick_check_accepts_the_same_second_when_either_side_has_no_fraction():
    assert _quick_check_in_sync(entry(5, OLD + 0.5), entry(5, OLD))
    assert _quick_check_in_sync(entry(5, OLD), entry(5, OLD + 0.5))


def test_quick_check_rejects_times_a_second_apart_and_other_sizes():
    assert not _quick_check_in_sync(entry(5, OLD + 0.5), entry(5, OLD + 1))
    assert not _quick_check_in_sync(entry(5, OLD), entry(5, OLD + 1))
    assert not _quick_check_in_sync(entry(5, OLD + 0.5), entry(6, OLD))


def test_quick_check_in_a_sync_compares_the_stored_second_with_the_fraction(tmp_path):
    source = local_tree(tmp_path, mtime=OLD + 0.75)
    target = RemoteMem("/t")
    target.mkdir()
    for name, content in (("a.txt", "a.txt"), ("d/b.txt", "d/b.txt")):
        path = target / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)
        path._utime(mtime=OLD)
    RemoteMem.reads.clear()

    syncer = PathSyncer()
    seen = events_of(syncer)
    syncer.sync(source, target)

    assert SyncEvent.Copy not in seen
    assert RemoteMem.reads == []


def test_quick_check_in_a_sync_reads_files_whose_times_are_a_second_apart(tmp_path):
    source = local_tree(tmp_path, mtime=OLD + 0.75)
    target = RemoteMem("/t")
    target.mkdir()
    for name, content in (("a.txt", "a.txt"), ("d/b.txt", "d/b.txt")):
        path = target / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)
        path._utime(mtime=OLD + 1)
    RemoteMem.reads.clear()

    PathSyncer().sync(source, target)

    assert sorted(RemoteMem.reads) == ["/t/a.txt", "/t/d/b.txt"]


def test_touch_moves_the_time_to_about_now():
    root = MemPath("/m")
    root.mkdir()
    (root / "f").write_text("x")
    (root / "f")._utime(mtime=OLD)
    before = time.time()

    (root / "f").touch()

    assert before - 1 <= (root / "f").stat().st_mtime <= time.time() + 1
