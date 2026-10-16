"""`AsyncPath` and `AsyncFile`: what runs where, what is unwrapped and
wrapped, how listings are pulled, and what a cancelled await leaves behind."""

from __future__ import annotations

import asyncio
import inspect
import os
import pathlib
import subprocess
import sys
import threading

import pytest

from waits import wait_until

import pathapi
from pathapi import AsyncFile, AsyncPath, LocalPath, MemPath, Path
from pathapi.mempath import MemPathBackend
from pathapi.testing import populate_fixture_tree


class GatedMem(MemPath):
    """A `MemPath` whose `stat()` waits for `gate` and records the thread."""

    gate = threading.Event()
    seen: "list[tuple[int, bool]]" = []

    def stat(self, *, follow_symlinks=True):
        type(self).seen.append((threading.get_ident(), type(self).gate.wait(10)))
        return MemPath.stat(self, follow_symlinks=follow_symlinks)


@pytest.fixture(autouse=True)
def _reset():
    GatedMem.gate = threading.Event()
    GatedMem.seen = []


def _tree(cls=MemPath):
    return populate_fixture_tree(cls("/", backend=MemPathBackend()))


# --- construction -------------------------------------------------------------


def test_a_text_or_a_standard_library_path_is_refused():
    with pytest.raises(TypeError):
        AsyncPath("a/b")
    with pytest.raises(TypeError):
        AsyncPath(pathlib.Path("a"))


def test_wrapping_an_async_path_wraps_what_it_wraps():
    inner = MemPath("/a")
    assert AsyncPath(AsyncPath(inner)).path is inner


def test_the_constructor_does_no_io():
    class Counting(MemPath):
        calls = 0

        def stat(self, *, follow_symlinks=True):
            type(self).calls += 1
            return MemPath.stat(self, follow_symlinks=follow_symlinks)

    path = AsyncPath(Counting("/a")) / "b"
    path.parent, path.name, str(path), repr(path)
    assert Counting.calls == 0


def test_equality_hash_order_and_text_follow_the_wrapped_path():
    one, two = AsyncPath(MemPath("/a")), AsyncPath(MemPath("/b"))
    assert one == AsyncPath(MemPath("/a")) and hash(one) == hash(AsyncPath(one.path))
    assert one != two and one < two and two > one and one <= one and two >= one
    assert one != MemPath("/a")
    assert str(one) == "/a" and repr(one) == "AsyncPath(MemPath('/a'))"
    with pytest.raises(TypeError):
        one < MemPath("/b")


def test_a_string_on_the_left_of_a_slash_makes_an_async_path():
    assert ("x" / AsyncPath(LocalPath("y"))).path == LocalPath("x") / "y"


# --- where a call runs ----------------------------------------------------------


def test_a_call_runs_on_a_worker_thread_while_the_loop_goes_on():
    async def main():
        path = AsyncPath(_tree(GatedMem))
        task = asyncio.ensure_future((path / "a.txt").stat())
        await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        assert not task.done()  # the loop ran, and the worker is still waiting
        GatedMem.gate.set()
        await task
        return threading.get_ident()

    loop_thread = asyncio.run(main())
    assert GatedMem.seen
    assert all(waited for _thread, waited in GatedMem.seen)
    assert all(thread != loop_thread for thread, _waited in GatedMem.seen)


def test_the_library_never_sets_a_loop_policy_or_runs_a_loop(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the event loop machinery was touched")

    async def main():
        path = AsyncPath(_tree())
        for name in (
            "set_event_loop",
            "set_event_loop_policy",
            "new_event_loop",
            "run",
        ):
            monkeypatch.setattr(asyncio, name, refuse)
        await (path / "a.txt").read_text()
        await (path / "n.txt").write_text("n")
        [entry async for entry in path.iterdir()]
        async with (path / "n.txt").open("r") as file:
            await file.read()
        await (path / "sub").rm(recursive=True)

    asyncio.run(main())


# --- arguments and results ------------------------------------------------------


class Recording(MemPath):
    received: "list" = []

    def copy(self, target, **kwargs):
        type(self).received.append((target, kwargs))

    def resolve(self, strict=False):
        return self


def test_async_paths_given_to_a_method_reach_the_backend_unwrapped():
    Recording.received = []
    source = Recording("/a")
    target = Recording("/b")

    async def main():
        await AsyncPath(source).copy(AsyncPath(target), overwrite=True, max_workers=3)
        await AsyncPath(source).copy(target=AsyncPath(target))

    asyncio.run(main())
    (positional, kwargs), (keyword, none) = Recording.received
    assert positional is target and kwargs == {"overwrite": True, "max_workers": 3}
    assert keyword is target and none == {}


def test_a_path_a_method_returns_is_an_async_path_and_other_values_stay():
    async def main():
        path = AsyncPath(_tree(Recording))
        resolved = await path.resolve()
        assert isinstance(resolved, AsyncPath) and resolved.path == path.path
        assert not isinstance(await (path / "a.txt").stat(), AsyncPath)

    asyncio.run(main())


def test_the_pure_methods_take_and_give_async_paths():
    root = MemPath("/", backend=MemPathBackend())
    base = AsyncPath(root / "a" / "b")
    assert base.relative_to(AsyncPath(root / "a")).path == MemPath("b")
    assert base.is_relative_to(AsyncPath(root / "a"))
    assert base.joinpath(AsyncPath(MemPath("c"))).path == root / "a" / "b" / "c"
    assert base.with_segments("x").path == MemPath("x")
    assert isinstance(base.parents[0], AsyncPath) and base.parents[0].name == "a"


def test_each_member_of_a_path_is_here_with_its_signature():
    here = set(dir(AsyncPath))
    public = {name for name in dir(Path) if not name.startswith("_")}
    assert public - here == {"info"}
    for name in sorted(public & here):
        sync = getattr(Path, name)
        if not callable(sync):
            continue
        assert str(inspect.signature(getattr(AsyncPath, name))) == str(
            inspect.signature(sync)
        ), name
        is_async = inspect.iscoroutinefunction(getattr(AsyncPath, name))
        assert is_async == (name not in PLAIN), name


PLAIN = {
    "as_posix",
    "as_uri",
    "full_match",
    "glob",
    "has_glob_pattern",
    "is_absolute",
    "is_hidden",
    "is_relative_to",
    "iterdir",
    "joinpath",
    "match",
    "open",
    "relative_to",
    "rglob",
    "walk",
    "with_name",
    "with_segments",
    "with_stem",
    "with_suffix",
}


def test_a_method_the_wrapped_class_lacks_raises_when_awaited():
    class Bare(MemPath):
        pass

    target = Bare("/a")

    async def main():
        path = AsyncPath(target)
        call = path.rename(Bare("/b"))  # nothing runs yet
        with pytest.raises(NotImplementedError):
            await call
        with pytest.raises(AttributeError):
            await AsyncPath(target).checksum()

    asyncio.run(main())


# --- listings -------------------------------------------------------------------


class Counted(MemPath):
    pulled = 0
    closed = False
    fail_after = None

    def iterdir(self):
        try:
            for index in range(100):
                if type(self).fail_after == index:
                    raise OSError(5, "listing broke")
                type(self).pulled += 1
                yield self.with_segments(f"/entry{index}")
        finally:
            type(self).closed = True


@pytest.fixture
def counted():
    Counted.pulled, Counted.closed, Counted.fail_after = 0, False, None
    return AsyncPath(Counted("/"))


def test_a_listing_is_read_a_batch_at_a_time_in_order(counted):
    async def main():
        entries = counted.iterdir()
        first = await entries.__anext__()
        assert first.name == "entry0"
        assert Counted.pulled == 32
        names = [first.name] + [entry.name async for entry in entries]
        assert names == [f"entry{index}" for index in range(100)]
        assert Counted.pulled == 100 and Counted.closed

    asyncio.run(main())


def test_a_listing_is_not_started_until_it_is_iterated(counted):
    entries = counted.iterdir()
    assert Counted.pulled == 0
    del entries


def test_aclose_stops_a_listing_and_closes_the_sync_iterator(counted):
    async def main():
        entries = counted.iterdir()
        await entries.__anext__()
        await entries.aclose()
        await entries.aclose()
        assert Counted.closed
        with pytest.raises(StopAsyncIteration):
            await entries.__anext__()

    asyncio.run(main())


def test_the_entries_before_a_failing_listing_are_delivered_then_it_raises(counted):
    Counted.fail_after = 5

    async def main():
        entries = counted.iterdir()
        names = []
        with pytest.raises(OSError):
            async for entry in entries:
                names.append(entry.name)
        assert names == [f"entry{index}" for index in range(5)]
        with pytest.raises(StopAsyncIteration):
            await entries.__anext__()

    asyncio.run(main())


def test_a_failing_first_step_raises_at_the_first_step(counted):
    Counted.fail_after = 0

    async def main():
        entries = counted.iterdir()
        with pytest.raises(OSError):
            await entries.__anext__()

    asyncio.run(main())


def test_walk_hands_over_one_directory_at_a_time_so_pruning_works():
    seen = []

    class Walked(MemPath):
        def _scandir(self):
            seen.append(self.name)
            return MemPath._scandir(self)

    async def main():
        root = AsyncPath(_tree(Walked))
        async for path, dirs, _files in root.walk():
            if path.name == "":
                assert seen == [""]  # the next directory is not listed yet
                dirs[:] = ["empty_dir"]

    asyncio.run(main())
    assert seen == ["", "empty_dir"]


# --- files ----------------------------------------------------------------------


class Gated:
    """A file whose `close()` waits for `release`."""

    def __init__(self):
        self.release = threading.Event()
        self.closed = False

    def close(self):
        self.release.wait(10)
        self.closed = True


def test_aclose_finishes_the_close_when_the_awaiting_task_is_cancelled():
    gated = Gated()

    async def main():
        task = asyncio.ensure_future(AsyncFile(gated).aclose())
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not gated.closed and not task.done()
        gated.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert gated.closed


def test_a_file_opened_by_a_cancelled_await_is_closed_when_the_worker_returns():
    gate = threading.Event()
    opened = Gated()
    opened.release.set()

    class Slow(MemPath):
        def open(self, *args, **kwargs):
            gate.wait(10)
            return opened

    async def _open(path):
        return await AsyncPath(path).open("rb")

    async def main():
        task = asyncio.ensure_future(_open(Slow("/a")))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        for _ in range(500):
            if opened.closed:
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert opened.closed


def test_a_cancelled_call_is_still_carried_out_by_its_worker():
    class Slow(MemPath):
        done = threading.Event()
        gate = threading.Event()

        def touch(self, *args, **kwargs):
            type(self).gate.wait(10)
            MemPath.touch(self, *args, **kwargs)
            type(self).done.set()

    async def main():
        path = AsyncPath(_tree(Slow)) / "late.txt"
        task = asyncio.ensure_future(path.touch())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        Slow.gate.set()

    asyncio.run(main())
    assert wait_until(Slow.done.is_set)


def test_a_file_reports_its_state_without_a_worker_thread(tmp_path):
    (tmp_path / "f.txt").write_text("x")

    async def main():
        async with AsyncPath(LocalPath(tmp_path / "f.txt")).open("rb") as file:
            assert file.readable() and not file.writable() and not file.closed
            assert file.mode == "rb" and file.name.endswith("f.txt")
            assert file.file.closed is False
        assert file.closed

    asyncio.run(main())


def test_the_package_exports_the_async_names():
    assert pathapi.AsyncPath is AsyncPath and pathapi.AsyncFile is AsyncFile


def test_importing_the_root_does_not_load_asyncio_until_asynchronous_use():
    source = str(pathlib.Path(pathapi.__file__).resolve().parent.parent)
    code = (
        "import sys, pathapi; "
        "before = 'asyncio' in sys.modules; "
        "pathapi.AsyncPath; "
        "print(before, 'asyncio' in sys.modules)"
    )
    env = dict(os.environ, PYTHONPATH=source)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.stdout.split() == ["False", "True"], result.stderr
