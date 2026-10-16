"""`max_workers` on recursive `copy()`, `rm()` and `PathSyncer`: a class that
declares `_threadsafe` is worked on by several threads, any other is not, and
the errors, the order of removals and the callbacks mean what they mean
serially."""

from __future__ import annotations

import threading
import time

import pytest

from pathapi import LocalPath
from pathapi._fanout import Fanout
from pathapi.mempath import MemPath, MemPathBackend
from pathapi.sync import PathSyncer, SyncEvent


class _Probe:
    """What the calls of one test saw: the threads, and the most that ran at
    once."""

    def __init__(self):
        self.lock = threading.Lock()
        self.threads = set()
        self.running = 0
        self.peak = 0
        self.log = []

    def enter(self, label):
        with self.lock:
            self.threads.add(threading.get_ident())
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.log.append(label)

    def leave(self):
        with self.lock:
            self.running -= 1


PROBE = _Probe()


class SlowMem(MemPath):
    """A `MemPath` that says it may be used from several threads and takes a
    moment to open a file, as a network round trip does."""

    _threadsafe = True
    fail_on = ()

    def _open(self, mode="r", buffering=-1):
        PROBE.enter(("open", mode, self.name))
        try:
            time.sleep(0.002)
            if self.name in type(self).fail_on:
                raise PermissionError(13, "denied", str(self))
            return super()._open(mode, buffering)
        finally:
            PROBE.leave()

    def unlink(self, missing_ok=False):
        PROBE.enter(("unlink", self.as_posix()))
        try:
            time.sleep(0.001)
            return super().unlink(missing_ok)
        finally:
            PROBE.leave()

    def rmdir(self):
        PROBE.enter(("rmdir", self.as_posix()))
        try:
            return super().rmdir()
        finally:
            PROBE.leave()


class SerialMem(SlowMem):
    _threadsafe = False


@pytest.fixture(autouse=True)
def _fresh():
    global PROBE
    PROBE = _Probe()
    SlowMem.fail_on = ()


def _tree(cls, dirs=4, files=6):
    root = cls("/", backend=MemPathBackend()) / "src"
    root.mkdir()
    for i in range(dirs):
        (root / f"d{i}").mkdir()
        (root / f"d{i}" / "deep").mkdir()
        (root / f"d{i}" / "deep" / "x.txt").write_text(f"x{i}")
        for j in range(files):
            (root / f"d{i}" / f"f{j}.txt").write_text(f"{i}-{j}")
    (root / "top.txt").write_text("top")
    return root


def _snapshot(root):
    found = {}
    for directory, dirs, files in root.walk():
        for name in files:
            found[(directory / name).relative_to(root).as_posix()] = (
                directory / name
            ).read_text()
        for name in dirs:
            found[(directory / name).relative_to(root).as_posix() + "/"] = None
    return found


# --- the argument ------------------------------------------------------------


@pytest.mark.parametrize("bad, error", [(0, ValueError), (-3, ValueError)])
def test_a_max_workers_below_one_is_a_value_error_before_anything_is_done(bad, error):
    src = _tree(SlowMem)
    with pytest.raises(error):
        src.copy(src.parent / "dst", recursive=True, max_workers=bad)
    assert not (src.parent / "dst").exists()
    with pytest.raises(error):
        src.rm(recursive=True, max_workers=bad)
    assert (src / "top.txt").exists()
    with pytest.raises(error):
        PathSyncer(max_workers=bad)


@pytest.mark.parametrize("bad", ["4", 2.5, True])
def test_a_max_workers_that_is_not_an_int_is_a_type_error(bad):
    src = _tree(SlowMem)
    with pytest.raises(TypeError):
        src.copy(src.parent / "dst", recursive=True, max_workers=bad)
    with pytest.raises(TypeError):
        src.rm(recursive=True, max_workers=bad)
    with pytest.raises(TypeError):
        PathSyncer(max_workers=bad)


# --- who works ----------------------------------------------------------------


def test_without_max_workers_everything_runs_on_the_calling_thread():
    src = _tree(SlowMem)
    src.copy(src.parent / "dst", recursive=True)
    PathSyncer().sync(src, src.parent / "mirror")
    (src.parent / "dst").rm(recursive=True)
    assert PROBE.threads == {threading.get_ident()}


@pytest.mark.parametrize("workers", [None, 1])
def test_one_worker_is_the_serial_operation(workers):
    src = _tree(SlowMem)
    src.copy(src.parent / "dst", recursive=True, max_workers=workers)
    assert PROBE.threads == {threading.get_ident()}


def test_a_class_that_does_not_declare_threads_is_worked_on_serially():
    src = _tree(SerialMem)
    src.copy(src.parent / "dst", recursive=True, max_workers=8)
    PathSyncer(max_workers=8).sync(src, src.parent / "mirror")
    (src.parent / "dst").rm(recursive=True, max_workers=8)
    assert PROBE.threads == {threading.get_ident()}


def test_a_copy_between_a_declared_and_an_undeclared_class_is_serial():
    src = _tree(SlowMem)
    other = SerialMem("/", backend=MemPathBackend()) / "dst"
    src.copy(other, recursive=True, max_workers=8)
    assert PROBE.threads == {threading.get_ident()}
    assert _snapshot(other) == _snapshot(src)


def test_a_copy_with_workers_uses_them_and_never_more_than_asked():
    src = _tree(SlowMem)
    dst = src.parent / "dst"
    src.copy(dst, recursive=True, max_workers=3)
    assert len(PROBE.threads) > 1
    assert 1 < PROBE.peak <= 3
    assert _snapshot(dst) == _snapshot(src)


def test_an_rm_with_workers_removes_the_tree_on_several_threads():
    src = _tree(SlowMem)
    src.rm(recursive=True, max_workers=4)
    assert not src.exists()
    assert len(PROBE.threads) > 1
    assert PROBE.peak <= 4


def test_a_sync_with_workers_copies_the_tree_on_several_threads():
    src = _tree(SlowMem)
    dst = src.parent / "mirror"
    PathSyncer(max_workers=4).sync(src, dst)
    assert len(PROBE.threads) > 1
    assert PROBE.peak <= 4
    assert _snapshot(dst) == _snapshot(src)


def test_a_local_tree_is_copied_removed_and_synced_with_workers(tmp_path):
    source = tmp_path / "s"
    for i in range(3):
        (source / f"d{i}").mkdir(parents=True)
        for j in range(5):
            (source / f"d{i}" / f"f{j}").write_text(f"{i}{j}")
    LocalPath(source).copy(LocalPath(tmp_path / "c"), recursive=True, max_workers=4)
    PathSyncer(max_workers=4).sync(LocalPath(source), LocalPath(tmp_path / "y"))
    for name in ("c", "y"):
        assert sorted(
            p.relative_to(tmp_path / name) for p in (tmp_path / name).rglob("*")
        ) == sorted(p.relative_to(source) for p in source.rglob("*"))
    LocalPath(tmp_path / "c").rm(recursive=True, max_workers=4)
    assert not (tmp_path / "c").exists()


# --- errors ---------------------------------------------------------------------


def test_a_copy_error_is_raised_and_what_was_started_is_finished():
    src = _tree(SlowMem)
    SlowMem.fail_on = ("f3.txt",)
    with pytest.raises(PermissionError):
        src.copy(src.parent / "dst", recursive=True, max_workers=4)
    assert PROBE.running == 0


def test_ignore_error_skips_the_entry_and_copies_the_rest():
    src = _tree(SlowMem)
    expected = {k: v for k, v in _snapshot(src).items() if not k.endswith("f3.txt")}
    SlowMem.fail_on = ("f3.txt",)
    seen = []
    dst = src.parent / "dst"
    src.copy(dst, recursive=True, max_workers=4, ignore_error=seen.append)
    assert len(seen) == 4 and all(isinstance(e, PermissionError) for e in seen)
    SlowMem.fail_on = ()
    assert {k: v for k, v in _snapshot(dst).items()} == expected


def test_the_on_error_predicate_is_asked_one_call_at_a_time():
    src = _tree(SlowMem)
    SlowMem.fail_on = tuple(f"f{j}.txt" for j in range(6))
    inside = []
    asked = []

    def predicate(error, path):
        inside.append(1)
        assert len(inside) == 1
        time.sleep(0.002)
        asked.append(path.name)
        inside.pop()
        return True

    src.copy(src.parent / "dst", recursive=True, max_workers=4, on_error=predicate)
    assert len(asked) == 24


def test_a_declined_error_ends_the_copy_with_that_error():
    src = _tree(SlowMem)
    SlowMem.fail_on = ("f2.txt",)
    with pytest.raises(PermissionError):
        src.copy(
            src.parent / "dst",
            recursive=True,
            max_workers=4,
            on_error=lambda error, path: False,
        )


def test_progress_is_called_one_call_at_a_time():
    src = _tree(SlowMem)
    inside = []

    def progress(path, copied, total):
        inside.append(1)
        assert len(inside) == 1
        time.sleep(0.001)
        inside.pop()

    src.copy(src.parent / "dst", recursive=True, max_workers=4, progress=progress)


def test_an_rm_error_goes_through_ignore_error_and_the_rest_is_removed():
    src = _tree(SlowMem)
    seen = []

    original = SlowMem.unlink

    def unlink(self, missing_ok=False):
        if self.name == "f1.txt":
            raise PermissionError(13, "denied", str(self))
        return original(self, missing_ok)

    SlowMem.unlink = unlink
    try:
        src.rm(
            recursive=True,
            max_workers=4,
            ignore_error=lambda error, path: seen.append(path.name) or True,
        )
    finally:
        SlowMem.unlink = original
    remaining = sorted(p.name for p in src.iterdir())
    assert remaining == [f"d{i}" for i in range(4)]
    assert sorted(seen).count("f1.txt") == 4


def test_a_declined_rm_error_ends_the_removal_with_that_error():
    src = _tree(SlowMem)
    original = SlowMem.unlink

    def unlink(self, missing_ok=False):
        if self.name == "f1.txt":
            raise PermissionError(13, "denied", str(self))
        return original(self, missing_ok)

    SlowMem.unlink = unlink
    try:
        with pytest.raises(PermissionError):
            src.rm(recursive=True, max_workers=4)
    finally:
        SlowMem.unlink = original
    assert src.exists()


def test_every_directory_is_removed_after_what_it_holds():
    src = _tree(SlowMem)
    src.rm(recursive=True, max_workers=4)
    removed = [entry for entry in PROBE.log if entry[0] in ("unlink", "rmdir")]
    for index, (kind, name) in enumerate(removed):
        if kind != "rmdir":
            continue
        later = [n for k, n in removed[index + 1 :] if n.startswith(name + "/")]
        assert later == [], name


def test_a_sync_error_goes_through_ignore_error_and_the_rest_is_synced():
    src = _tree(SlowMem)
    expected = {k: v for k, v in _snapshot(src).items() if not k.endswith("f3.txt")}
    SlowMem.fail_on = ("f3.txt",)
    errors = []
    dst = src.parent / "mirror"
    PathSyncer(
        max_workers=4,
        ignore_error=lambda error, source, target, event: errors.append(error) or True,
    ).sync(src, dst)
    assert len(errors) == 4
    SlowMem.fail_on = ()
    assert _snapshot(dst) == expected


def test_a_sync_error_that_is_not_tolerated_is_raised():
    src = _tree(SlowMem)
    SlowMem.fail_on = ("f3.txt",)
    with pytest.raises(PermissionError):
        PathSyncer(max_workers=4).sync(src, src.parent / "mirror")
    assert PROBE.running == 0


def test_the_sync_hook_is_called_one_call_at_a_time():
    src = _tree(SlowMem)
    inside = []
    events = []

    def hook(source, target, event, dry_run):
        inside.append(1)
        assert len(inside) == 1
        time.sleep(0.0005)
        events.append(event)
        inside.pop()

    PathSyncer(max_workers=4, hook=hook).sync(src, src.parent / "mirror")
    assert events.count(SyncEvent.Copy) == 4 * 6 + 4 + 1


def test_a_dry_run_sync_with_workers_changes_nothing():
    src = _tree(SlowMem)
    dst = src.parent / "mirror"
    PathSyncer(max_workers=4).sync(src, dst, dry_run=True)
    assert not dst.exists()


def test_a_sync_with_remove_missing_and_workers_matches_the_source():
    src = _tree(SlowMem)
    dst = src.parent / "mirror"
    PathSyncer().sync(src, dst)
    (dst / "stale.txt").write_text("stale")
    (src / "d0" / "f0.txt").write_text("changed")
    PathSyncer(max_workers=4, remove_missing=True).sync(src, dst)
    assert _snapshot(dst) == _snapshot(src)


def test_the_ignore_error_of_an_rm_is_asked_one_call_at_a_time():
    src = _tree(SlowMem)
    original = SlowMem.unlink

    def unlink(self, missing_ok=False):
        if self.name.startswith("f"):
            raise PermissionError(13, "denied", str(self))
        return original(self, missing_ok)

    inside = []
    asked = []

    def handler(error, path):
        inside.append(1)
        assert len(inside) == 1
        time.sleep(0.002)
        asked.append(path.name)
        inside.pop()
        return True

    SlowMem.unlink = unlink
    try:
        src.rm(recursive=True, max_workers=4, ignore_error=handler)
    finally:
        SlowMem.unlink = original
    assert len([name for name in asked if name.startswith("f")]) == 24


def test_a_batch_raises_the_first_error_of_its_tasks():
    def fail():
        raise KeyError("task")

    with Fanout(2) as fan:
        with pytest.raises(KeyError):
            with fan.batch() as batch:
                batch.submit(fail)
        assert isinstance(fan.error, KeyError)


def test_a_batch_skips_the_tasks_not_started_after_an_error():
    ran = []

    def fail():
        raise KeyError("task")

    with Fanout(1) as fan:
        with pytest.raises(KeyError):
            with fan.batch() as batch:
                batch.submit(fail)
                batch.submit(ran.append, 1)
                batch.submit(ran.append, 2)
    assert ran == []
