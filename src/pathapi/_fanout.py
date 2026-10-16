"""A bounded pool for the independent leaves of one tree operation.

`copy(recursive=True)`, `rm(recursive=True)` and `PathSyncer` walk a tree on the
thread that was called, directory by directory, and hand the files of each
directory to the pool. A pool thread never waits for another, so the pool
cannot deadlock however deep the tree is.
"""

from __future__ import annotations

import concurrent.futures as _futures
import contextvars as _contextvars
import threading as _threading
import typing as _ty

__all__ = ["Fanout", "check_workers", "workers_for"]

#: The fan-out of the `copy()` running on this thread, for the `copy()` of each
#: subdirectory to join (its keywords are those of a child, which an override
#: may not take `max_workers` in).
ACTIVE: "_contextvars.ContextVar[Fanout | None]" = _contextvars.ContextVar(
    "pathapi_fanout", default=None
)


def check_workers(max_workers: object) -> None:
    """`ValueError`/`TypeError` unless `max_workers` is None or an int >= 1."""
    if max_workers is None:
        return
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise TypeError(
            f"max_workers must be an int or None, got {type(max_workers).__name__}"
        )
    if max_workers < 1:
        raise ValueError(f"max_workers must be at least 1, got {max_workers}")


def workers_for(max_workers: "int | None", *paths: object) -> int:
    """How many threads an operation on `paths` may use: `max_workers`, or 1
    (serial) when it is None or when the class of any path does not declare
    `_threadsafe`."""
    check_workers(max_workers)
    if max_workers is None or max_workers == 1:
        return 1
    if all(getattr(type(path), "_threadsafe", False) for path in paths):
        return max_workers
    return 1


class Fanout:
    """The pool of one operation, and the first error a task raised.

    `lock` serializes the callbacks of the caller (`on_error`, `progress`,
    hooks), which then never run at the same time."""

    def __init__(self, workers: int) -> None:
        self.owner = _threading.get_ident()
        self.lock = _threading.RLock()
        self._error: "BaseException | None" = None
        self._pool = _futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="pathapi-fanout"
        )

    def __enter__(self) -> "Fanout":
        return self

    def __exit__(self, *exc: object) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)

    def owned(self) -> bool:
        """Whether the calling thread is the one that started the operation,
        the only one that walks directories and submits."""
        return _threading.get_ident() == self.owner

    def batch(self) -> "Batch":
        return Batch(self)

    def fail(self, error: BaseException) -> None:
        """Record `error` as the one that ends the operation, unless one is
        recorded already; tasks not started yet are skipped."""
        with self.lock:
            if self._error is None:
                self._error = error

    def check(self) -> None:
        """Raise the error that ended the operation, if one did."""
        if self._error is not None:
            raise self._error

    @property
    def error(self) -> "BaseException | None":
        return self._error

    def _run(self, function: _ty.Callable[..., object], args: tuple) -> None:
        if self._error is not None:
            return
        try:
            function(*args)
        except BaseException as error:
            self.fail(error)


class Batch:
    """The tasks of one directory. Leaving the block waits for all of them
    and raises the first error any task of the operation raised."""

    def __init__(self, fan: Fanout) -> None:
        self._fan = fan
        self._futures: "list[_futures.Future]" = []

    def submit(self, function: _ty.Callable[..., object], *args: object) -> None:
        self._futures.append(self._fan._pool.submit(self._fan._run, function, args))

    def __enter__(self) -> "Batch":
        return self

    def __exit__(self, kind, error, traceback) -> bool:
        if error is not None:
            self._fan.fail(error)
        _futures.wait(self._futures)
        if error is None and self._fan.error is not None:
            raise self._fan.error
        return False
