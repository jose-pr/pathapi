"""The contract of `AsyncPath` over a path: each call gives what the sync call gives."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from ..path import AsyncFile, AsyncPath
from ..protocols import (
    Hardlink,
    Move,
    Readlink,
    Symlink,
    capabilities,
    supports,
)
from ._fixtures import FIXTURE_TREE
from ._read import _Switches


class AsyncPathContract(_Switches):
    """Contract tests for `AsyncPath` over the path classes of a backend.

    Subclasses provide a `root` fixture: a **sync** path (a fresh,
    function-scoped directory holding the standard tree, as for
    `PathContract`), which each test wraps in `AsyncPath`. The coroutines run
    with `asyncio.run()` inside ordinary test functions, so no pytest plugin
    is needed. Every test compares what the async call returns or raises with
    what the sync call on the same backend returns or raises.

    Capability attributes: `supports_listing`, `supports_mkdir`,
    `supports_delete`, `supports_rename` (all default `None`: ask
    `supports()` on `root`), `supports_move` (`True`) and
    `supports_concurrent_use` (`None`: the class's `_threadsafe`), as for
    `PathContract`.
    """

    supports_listing = None
    supports_mkdir = None
    supports_delete = None
    supports_rename = None
    supports_move = True
    supports_concurrent_use = None

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    def test_the_pure_members_are_plain_and_give_async_paths(self, root):
        path = AsyncPath(root) / "sub" / "c.py"
        assert isinstance(path, AsyncPath)
        assert path.path == root / "sub" / "c.py"
        assert (path.name, path.suffix, path.stem) == ("c.py", ".py", "c")
        assert path.parent == AsyncPath(root / "sub")
        assert path.with_name("e.py").path == root / "sub" / "e.py"
        assert path.joinpath("x").path == root / "sub" / "c.py" / "x"
        assert path.match("*.py")
        assert path.relative_to(AsyncPath(root)).path == (
            root / "sub" / "c.py"
        ).relative_to(root)
        assert all(isinstance(p, AsyncPath) for p in path.parents)
        assert path != root / "sub" / "c.py"

    def test_the_io_methods_are_coroutine_functions(self, root):
        for name in ("stat", "exists", "read_text", "write_text", "rm", "copy"):
            assert inspect.iscoroutinefunction(getattr(AsyncPath, name)), name
        for name in ("name", "parent", "joinpath", "match", "iterdir", "walk", "open"):
            assert not inspect.iscoroutinefunction(getattr(AsyncPath, name)), name

    def test_exists_and_the_type_tests_answer_as_the_sync_path_does(self, root):
        async def main():
            for name in ("a.txt", "sub", "missing", "sub/missing/x"):
                path = root.joinpath(*name.split("/"))
                async_path = AsyncPath(path)
                assert await async_path.exists() == path.exists(), name
                assert await async_path.is_dir() == path.is_dir(), name
                assert await async_path.is_file() == path.is_file(), name
                assert await async_path.is_symlink() == path.is_symlink(), name

        self._run(main())

    def test_stat_gives_the_sync_size_and_type_and_raises_the_same_error(self, root):
        async def main():
            stat = await AsyncPath(root / "a.txt").stat()
            expected = (root / "a.txt").stat()
            assert (stat.st_size, stat.st_mode & 0o170000) == (
                expected.st_size,
                expected.st_mode & 0o170000,
            )
            with pytest.raises(FileNotFoundError):
                await AsyncPath(root / "missing").stat()
            with pytest.raises(FileNotFoundError):
                await AsyncPath(root / "missing").read_text()

        self._run(main())

    def test_reads_and_writes_round_trip_with_the_sync_path(self, root):
        async def main():
            text = AsyncPath(root / "written.txt")
            await text.write_text("hello")
            assert (root / "written.txt").read_text() == "hello"
            assert await text.read_text() == "hello"
            data = AsyncPath(root / "written.bin")
            await data.write_bytes(b"\x00\x01\xff")
            assert (root / "written.bin").read_bytes() == b"\x00\x01\xff"
            assert await data.read_bytes() == b"\x00\x01\xff"
            assert await AsyncPath(root / "a.txt").read_text() == "a"

        self._run(main())

    def test_an_opened_file_reads_lines_and_seeks_like_the_sync_file(self, root):
        (root / "lines.txt").write_text("one\ntwo\nthree\n")

        async def main():
            async with AsyncPath(root / "lines.txt").open("r") as file:
                assert isinstance(file, AsyncFile)
                assert await file.readline() == "one\n"
                assert await file.read(3) == "two"
                if file.seekable():
                    await file.seek(0)
                    assert await file.tell() == 0
                    assert [line async for line in file] == [
                        "one\n",
                        "two\n",
                        "three\n",
                    ]
            assert file.closed

        self._run(main())

    def test_a_file_opened_by_await_is_closed_by_aclose_twice(self, root):
        async def main():
            file = await AsyncPath(root / "a.txt").open("rb")
            assert await file.read() == b"a"
            await file.aclose()
            await file.aclose()
            assert file.closed

        self._run(main())

    def test_a_file_written_through_the_context_manager_is_complete_on_exit(self, root):
        async def main():
            async with AsyncPath(root / "out.bin").open("wb") as file:
                await file.write(b"abc")
                await file.write(b"def")

        self._run(main())
        assert (root / "out.bin").read_bytes() == b"abcdef"

    def test_open_of_a_missing_file_raises_when_awaited(self, root):
        async def main():
            opening = AsyncPath(root / "missing.txt").open("rb")
            with pytest.raises(FileNotFoundError):
                await opening
            with pytest.raises(FileNotFoundError):
                async with AsyncPath(root / "missing.txt").open("rb"):
                    pass

        self._run(main())

    def test_iterdir_lists_the_sync_names_as_async_paths(self, root):
        self._require("supports_listing", root)

        async def main():
            listed = [entry async for entry in AsyncPath(root / "sub").iterdir()]
            assert all(isinstance(entry, AsyncPath) for entry in listed)
            assert sorted(entry.name for entry in listed) == sorted(
                entry.name for entry in (root / "sub").iterdir()
            )
            with pytest.raises(OSError):
                async for _ in AsyncPath(root / "missing").iterdir():
                    pass

        self._run(main())

    def test_glob_and_rglob_give_the_sync_matches(self, root):
        self._require("supports_listing", root)

        async def main():
            top = AsyncPath(root)
            assert {p.path async for p in top.glob("*.py")} == set(root.glob("*.py"))
            assert {p.path async for p in top.rglob("*.py")} == set(root.rglob("*.py"))

        self._run(main())

    def test_walk_gives_the_sync_tree_and_honours_pruning(self, root):
        self._require("supports_listing", root)

        def shape(entries):
            return sorted((str(p), sorted(d), sorted(f)) for p, d, f in entries)

        async def main():
            walked = [
                (str(p.path), dirs, files)
                async for p, dirs, files in AsyncPath(root).walk()
            ]
            assert shape(walked) == shape(root.walk())
            seen = []
            async for p, dirs, _files in AsyncPath(root).walk():
                seen.append(p.name)
                if "sub" in dirs:
                    dirs.remove("sub")
            assert "nested" not in seen and "sub" not in seen[1:]

        self._run(main())

    def test_mkdir_and_rmdir_and_unlink_act_on_the_backend(self, root):
        self._require("supports_mkdir", root)
        self._require("supports_delete", root)

        async def main():
            directory = AsyncPath(root / "made")
            await directory.mkdir()
            assert (root / "made").is_dir()
            with pytest.raises(FileExistsError):
                await directory.mkdir()
            await directory.mkdir(exist_ok=True)
            await (directory / "f.txt").write_text("x")
            await (directory / "f.txt").unlink()
            assert not (root / "made" / "f.txt").exists()
            await (directory / "f.txt").unlink(missing_ok=True)
            await directory.rmdir()
            assert not (root / "made").exists()

        self._run(main())

    def test_rm_removes_a_tree_and_reports_a_missing_path(self, root):
        self._require("supports_delete", root)

        async def main():
            await AsyncPath(root / "sub").rm(recursive=True)
            assert not (root / "sub").exists()
            with pytest.raises(FileNotFoundError):
                await AsyncPath(root / "sub").rm(recursive=True)
            await AsyncPath(root / "sub").rm(recursive=True, missing_ok=True)

        self._run(main())

    def test_copy_and_copy_into_take_async_paths_and_return_one(self, root):
        self._require("supports_mkdir", root)

        async def main():
            source = AsyncPath(root / "a.txt")
            await source.copy(AsyncPath(root / "copy.txt"))
            assert (root / "copy.txt").read_text() == "a"
            with pytest.raises(FileExistsError):
                await source.copy(root / "copy.txt")
            copied = await source.copy_into(AsyncPath(root / "empty_dir"))
            assert isinstance(copied, AsyncPath)
            assert (root / "empty_dir" / "a.txt").read_text() == "a"
            await AsyncPath(root / "sub").copy(root / "sub2", recursive=True)
            assert (root / "sub2" / "nested" / "d.py").read_text() == "d"

        self._run(main())

    def test_a_recursive_copy_with_workers_gives_the_serial_result(self, root):
        self._require("supports_mkdir", root)
        self._require("supports_concurrent_use", root)

        async def main():
            await AsyncPath(root / "sub").copy(
                root / "w", recursive=True, max_workers=4
            )
            assert (root / "w" / "c.py").read_text() == "c"
            assert (root / "w" / "nested" / "d.py").read_text() == "d"
            await AsyncPath(root / "w").rm(recursive=True, max_workers=4)
            assert not (root / "w").exists()

        self._run(main())

    def test_move_and_rename_leave_the_content_at_the_target(self, root):
        self._require("supports_move")

        async def main():
            await AsyncPath(root / "b.py").move(AsyncPath(root / "moved.py"))
            assert not (root / "b.py").exists()
            assert (root / "moved.py").read_text() == "b"
            if self._has("supports_rename", root):
                await AsyncPath(root / "moved.py").rename(root / "renamed.py")
                assert (root / "renamed.py").read_text() == "b"

        self._run(main())

    def test_touch_creates_an_empty_file(self, root):
        self._require("supports_mkdir", root)

        async def main():
            await AsyncPath(root / "touched").touch()
            assert (root / "touched").read_bytes() == b""

        self._run(main())

    def test_a_method_of_a_missing_protocol_raises_what_the_sync_one_raises(self, root):
        async def main():
            for protocol, name, args in (
                (Move, "rename", (root / "x",)),
                (Readlink, "readlink", ()),
                (Symlink, "symlink_to", ("target",)),
                (Hardlink, "hardlink_to", (root / "a.txt",)),
            ):
                if supports(AsyncPath(root), protocol):
                    continue
                with pytest.raises(NotImplementedError):
                    await getattr(AsyncPath(root / "a.txt"), name)(*args)

        self._run(main())

    def test_supports_and_capabilities_answer_for_the_wrapped_path(self, root):
        wrapped = AsyncPath(root)
        assert capabilities(wrapped) == capabilities(root)
        for protocol in capabilities(root):
            assert supports(wrapped, protocol)

    def test_many_calls_at_once_each_get_their_own_result(self, root):
        async def main():
            names = ["a.txt", "b.py", "sub/c.py", "sub/nested/d.py"] * 4
            results = await asyncio.gather(
                *(
                    AsyncPath(root.joinpath(*name.split("/"))).read_text()
                    for name in names
                )
            )
            assert results == [FIXTURE_TREE[name] for name in names]

        self._run(main())

    def test_the_running_loop_is_left_open_and_in_place(self, root):
        async def main():
            loop = asyncio.get_running_loop()
            await AsyncPath(root / "a.txt").read_text()
            [entry async for entry in AsyncPath(root).walk()]
            assert asyncio.get_running_loop() is loop and not loop.is_closed()

        self._run(main())
