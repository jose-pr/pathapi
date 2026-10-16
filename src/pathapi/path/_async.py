"""`AsyncPath`: any `Path` used from an event loop, each call run on a worker thread."""

from __future__ import annotations

import asyncio as _asyncio
import inspect as _inspect
import itertools as _itertools
import threading as _threading
import typing as _ty

from ..protocols import NativeChecksum
from ..protocols import supports as _supports
from ._path import Path
from ._pure import Pathname

__all__ = ["AsyncFile", "AsyncPath"]

#: Entries a listing reads ahead in one worker-thread call.
_BATCH = 32


def _unwrap(value):
    return value._path if isinstance(value, AsyncPath) else value


def _wrap(value):
    return AsyncPath(value) if isinstance(value, Pathname) else value


def _invoke(path, name, args, kwargs):
    # The attribute is read on the worker thread: a class without the
    # method raises when the call is awaited, as the sync call raises.
    return getattr(path, name)(*args, **kwargs)


async def _finish(work) -> None:
    """Await `work` (a coroutine) to completion even when the caller is
    cancelled meanwhile, and then re-raise the cancellation."""
    task = _asyncio.ensure_future(work)
    try:
        await _asyncio.shield(task)
    except _asyncio.CancelledError:
        if not task.done():
            await _asyncio.wait([task])
        raise


# --- the coroutines and plain members, generated from the sync class ----------

#: I/O methods of `Path`: each becomes a coroutine of the same name and arguments.
_COROUTINES = (
    "absolute",
    "checksum",
    "chmod",
    "chown",
    "copy",
    "copy_into",
    "exists",
    "group",
    "hardlink_to",
    "is_block_device",
    "is_char_device",
    "is_dir",
    "is_dir_binding",
    "is_fifo",
    "is_file",
    "is_junction",
    "is_mount",
    "is_socket",
    "is_symlink",
    "lchmod",
    "lstat",
    "mkdir",
    "move",
    "move_into",
    "owner",
    "read_bytes",
    "read_text",
    "readlink",
    "rename",
    "replace",
    "resolve",
    "rm",
    "rmdir",
    "samefile",
    "stat",
    "supported_checksums",
    "symlink_to",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
)

#: Methods that do no I/O: plain, with `AsyncPath` arguments and results.
_PLAIN = (
    "as_posix",
    "as_uri",
    "full_match",
    "has_glob_pattern",
    "is_absolute",
    "is_hidden",
    "is_relative_to",
    "joinpath",
    "match",
    "relative_to",
    "with_name",
    "with_segments",
    "with_stem",
    "with_suffix",
)

#: Properties of `Pathname` that return what the wrapped path returns.
_VALUES = (
    "anchor",
    "drive",
    "name",
    "parts",
    "root",
    "segments",
    "stem",
    "suffix",
    "suffixes",
)


def _coroutine(name: str):
    async def method(self, *args, **kwargs):
        args = tuple(_unwrap(arg) for arg in args)
        kwargs = {key: _unwrap(value) for key, value in kwargs.items()}
        result = await _asyncio.to_thread(_invoke, self._path, name, args, kwargs)
        return _wrap(result)

    return _named(method, name)


def _plain(name: str):
    def method(self, *args, **kwargs):
        args = tuple(_unwrap(arg) for arg in args)
        kwargs = {key: _unwrap(value) for key, value in kwargs.items()}
        return _wrap(getattr(self._path, name)(*args, **kwargs))

    return _named(method, name)


def _value(name: str):
    def getter(self):
        return getattr(self._path, name)

    getter.__name__ = name
    return property(getter, doc=getattr(Pathname, name).__doc__)


def _named(method, name: str):
    method.__name__ = method.__qualname__ = name
    # The signature `inspect` shows is the sync method's.
    method.__wrapped__ = getattr(Path, name, None) or getattr(NativeChecksum, name)
    method.__doc__ = (
        "Coroutine: `Path.%s()` run on a worker thread." % name
        if _inspect.iscoroutinefunction(method)
        else "`Path.%s()`, with `AsyncPath` arguments and results." % name
    )
    return method


class AsyncPath:
    """A `Path` for use from an event loop: the pure members are plain and
    return `AsyncPath`, and every method that does I/O is a coroutine of the
    same name and arguments, awaited like the sync method is called.

    "Async" here means a worker thread: each call runs the wrapped path's
    sync method with `asyncio.to_thread()`, on the running loop's default
    executor, so the event loop stays free while a blocking backend waits.
    It never creates, closes or configures an event loop. Cancelling an
    `await` abandons the wait, not the call: the worker thread finishes it.
    Callbacks a method takes (`progress`, `on_error`, `ignore_error`,
    `rm(ignore_error=)`, a `walk()` `on_error`) run on worker threads.

    `AsyncPath(path)` stores `path`, any `Pathname` (a `Path` in practice),
    and does no I/O; `path` is the wrapped one. A method of a protocol the
    wrapped class lacks is still here and raises what the sync call raises
    when awaited. `info` and `__iter__` are not provided. `supports(p, ...)`
    and `capabilities(p)` of an `AsyncPath` answer for the wrapped path.
    Recursive `copy()`, `rm()` and `move()` are one worker-thread call, so
    `max_workers=` is the sync fan-out. `parents` is a tuple.

    Equality, hashing and ordering are those of the wrapped paths, and
    an `AsyncPath` never equals a plain path.
    """

    __slots__ = ("_path",)

    def __init__(self, path: Pathname) -> None:
        if isinstance(path, AsyncPath):
            path = path._path
        elif not isinstance(path, Pathname):
            raise TypeError(
                f"AsyncPath wraps a path of this package, not {type(path).__name__!r}"
            )
        self._path = path

    @property
    def path(self) -> Pathname:
        """The wrapped path."""
        return self._path

    def _supports(self, protocol: type) -> bool:
        return _supports(self._path, protocol)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path!r})"

    def __str__(self) -> str:
        return str(self._path)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AsyncPath):
            return NotImplemented
        return self._path == other._path

    def __hash__(self) -> int:
        return hash((AsyncPath, self._path))

    def __lt__(self, other):
        return (
            self._path < other._path if isinstance(other, AsyncPath) else NotImplemented
        )

    def __le__(self, other):
        return (
            self._path <= other._path
            if isinstance(other, AsyncPath)
            else NotImplemented
        )

    def __gt__(self, other):
        return (
            self._path > other._path if isinstance(other, AsyncPath) else NotImplemented
        )

    def __ge__(self, other):
        return (
            self._path >= other._path
            if isinstance(other, AsyncPath)
            else NotImplemented
        )

    def __truediv__(self, key):
        return AsyncPath(self._path / _unwrap(key))

    def __rtruediv__(self, key):
        return AsyncPath(_unwrap(key) / self._path)

    @property
    def parent(self) -> "AsyncPath":
        return AsyncPath(self._path.parent)

    @property
    def parents(self) -> "tuple[AsyncPath, ...]":
        return tuple(AsyncPath(parent) for parent in self._path.parents)

    def iterdir(self) -> "_Listing":
        """Async iterator over the entries of this directory, as `AsyncPath`.
        The listing starts when iteration does, and reads a batch of entries
        per worker-thread call."""
        return _Listing(lambda: self._path.iterdir(), _BATCH, _wrap)

    def glob(self, *args, **kwargs) -> "_Listing":
        """Async iterator over `Path.glob()`, as `AsyncPath`. A bad pattern
        raises on the first step of the iteration, not on this call."""
        args = tuple(_unwrap(arg) for arg in args)
        return _Listing(lambda: self._path.glob(*args, **kwargs), _BATCH, _wrap)

    def rglob(self, *args, **kwargs) -> "_Listing":
        """Async iterator over `Path.rglob()`, as `AsyncPath`."""
        args = tuple(_unwrap(arg) for arg in args)
        return _Listing(lambda: self._path.rglob(*args, **kwargs), _BATCH, _wrap)

    def walk(self, *args, **kwargs) -> "_Listing":
        """Async iterator over `Path.walk()`: `(AsyncPath, dirnames, filenames)`.
        One directory per worker-thread call, so editing `dirnames` in place
        prunes a top-down walk as it does for the sync one."""
        return _Listing(
            lambda: self._path.walk(*args, **kwargs),
            1,
            lambda entry: (_wrap(entry[0]), *entry[1:]),
        )

    def open(self, *args, **kwargs) -> "_Opening":
        """Open the file on a worker thread. `await path.open(...)` gives an
        `AsyncFile` to close with `aclose()`; `async with path.open(...) as f`
        closes it. The arguments are those of `Path.open()`."""
        return _Opening(lambda: self._path.open(*args, **kwargs))


for _name in _COROUTINES:
    setattr(AsyncPath, _name, _coroutine(_name))
for _name in _PLAIN:
    setattr(AsyncPath, _name, _plain(_name))
for _name in _VALUES:
    setattr(AsyncPath, _name, _value(_name))
for _name in ("iterdir", "glob", "rglob", "walk", "open"):
    getattr(AsyncPath, _name).__wrapped__ = getattr(Path, _name)
del _name


class _Listing:
    """An async iterator over a sync one, pulled on worker threads.

    `aclose()` closes the sync iterator; an iterator that ran to its end is
    already closed. Use it from one task at a time."""

    __slots__ = ("_make", "_batch", "_convert", "_source", "_ready", "_end", "_lock")

    def __init__(
        self,
        make: "_ty.Callable[[], _ty.Iterator]",
        batch: int,
        convert: "_ty.Callable[[_ty.Any], _ty.Any]",
    ) -> None:
        self._make = make
        self._batch = batch
        self._convert = convert
        self._source = None
        self._ready: "list" = []
        self._end: "BaseException | None" = None
        self._lock = _threading.Lock()

    def __aiter__(self) -> "_Listing":
        return self

    def _pull(self) -> None:
        # Items go in front of any a cancelled earlier call left behind.
        with self._lock:
            if self._source is None:
                self._source = iter(self._make())
            items = []
            failure = None
            try:
                items.extend(_itertools.islice(self._source, self._batch))
            except BaseException as error:
                failure = error
            self._ready[:0] = items[::-1]
            if failure is not None or len(items) < self._batch:
                self._end = failure or StopAsyncIteration()
                source, self._source = self._source, None
                close = getattr(source, "close", None)
                if close is not None:
                    close()
            if failure is not None and not items:
                self._end = StopAsyncIteration()
                raise failure

    async def __anext__(self):
        if not self._ready and self._end is None:
            await _asyncio.to_thread(self._pull)
        if self._ready:
            return self._convert(self._ready.pop())
        end, self._end = self._end, StopAsyncIteration()
        raise end

    async def aclose(self) -> None:
        """Stop the iteration and close the sync iterator."""
        self._ready = []
        self._end = StopAsyncIteration()
        source, self._source = self._source, None
        close = getattr(source, "close", None)
        if close is not None:
            await _finish(_asyncio.to_thread(self._close, close))

    def _close(self, close) -> None:
        with self._lock:
            close()


class _Opening:
    """What `AsyncPath.open()` returns: awaited, it opens; as an async
    context manager it opens and closes."""

    __slots__ = ("_make", "_file")

    def __init__(self, make) -> None:
        self._make = make
        self._file = None

    def __await__(self):
        return self._open().__await__()

    async def _open(self) -> "AsyncFile":
        task = _asyncio.ensure_future(_asyncio.to_thread(self._make))
        try:
            handle = await _asyncio.shield(task)
        except _asyncio.CancelledError:
            # The worker still opens the file: close it when it has.
            task.add_done_callback(_close_late)
            raise
        return AsyncFile(handle)

    async def __aenter__(self) -> "AsyncFile":
        self._file = await self._open()
        return self._file

    async def __aexit__(self, *exc) -> None:
        await self._file.aclose()


def _close_late(task) -> None:
    if not task.cancelled() and task.exception() is None:
        _asyncio.get_running_loop().run_in_executor(None, task.result().close)


def _file_method(name: str):
    async def method(self, *args):
        return await _asyncio.to_thread(self._call, name, args)

    method.__name__ = method.__qualname__ = name
    method.__doc__ = "Coroutine: `%s()` of the file, run on a worker thread." % name
    return method


class AsyncFile:
    """An open file whose reads and writes run on worker threads.

    The coroutines are those of the file the wrapped path opened: a binary or
    text stream. Iterate it with `async for` for its lines. `aclose()` ends
    it: it is complete when it returns and harmless to call twice, and it
    finishes even when the awaiting task is cancelled. One call at a time
    touches the file. `file` is the sync file; do not use it while a
    coroutine runs."""

    __slots__ = ("_file", "_lock")

    def __init__(self, file) -> None:
        self._file = file
        self._lock = _threading.Lock()

    @property
    def file(self):
        """The sync file object this wraps."""
        return self._file

    @property
    def closed(self) -> bool:
        return self._file.closed

    @property
    def name(self):
        return getattr(self._file, "name", None)

    @property
    def mode(self):
        return getattr(self._file, "mode", None)

    def readable(self) -> bool:
        return self._file.readable()

    def writable(self) -> bool:
        return self._file.writable()

    def seekable(self) -> bool:
        return self._file.seekable()

    def _call(self, name: str, args: tuple):
        with self._lock:
            return getattr(self._file, name)(*args)

    async def aclose(self) -> None:
        """Close the file."""
        await _finish(_asyncio.to_thread(self._call, "close", ()))

    def __aiter__(self) -> "AsyncFile":
        return self

    async def __anext__(self):
        line = await self.readline()
        if not line:
            raise StopAsyncIteration
        return line

    async def __aenter__(self) -> "AsyncFile":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


for _name in (
    "flush",
    "read",
    "readline",
    "readlines",
    "seek",
    "tell",
    "truncate",
    "write",
    "writelines",
):
    setattr(AsyncFile, _name, _file_method(_name))
del _name
