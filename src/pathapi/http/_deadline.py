"""A time limit for a whole request, above the per-wait timeout."""

from __future__ import annotations

import math as _math
import time as _time


def check_deadline(deadline) -> "float | None":
    """`deadline` as seconds, or `None` for no limit. `TypeError` for anything
    but a number or `None`, `ValueError` for a number that is not positive."""
    if deadline is None:
        return None
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise TypeError("a deadline is a number of seconds or None")
    if _math.isnan(deadline) or deadline <= 0:
        raise ValueError("a deadline is a positive number of seconds, or None")
    return float(deadline)


class Deadline:
    """The moment a request must be finished by. It lives for one request,
    redirects included."""

    __slots__ = ("_seconds", "_at", "_what")

    def __init__(self, seconds: float, what: str):
        self._seconds = seconds
        self._at = _time.monotonic() + seconds
        self._what = what

    def check(self) -> float:
        """The seconds that remain; `TimeoutError` once there are none."""
        left = self._at - _time.monotonic()
        if left <= 0:
            raise TimeoutError(
                f"the {self._seconds:g} s deadline of {self._what} passed"
            )
        return left

    def cap(self, timeout):
        """`timeout` (a number, `None` or a `(connect, read)` pair) with each
        wait held to the time that remains."""
        left = self.check()
        if isinstance(timeout, tuple):
            return tuple(self._capped(part, left) for part in timeout)
        return self._capped(timeout, left)

    @staticmethod
    def _capped(wait, left):
        return left if wait is None else min(wait, left)

    def bound(self, response):
        """Make `response` check the deadline before each read of its body."""
        source = getattr(response, "_source", None)
        if source is not None:
            response._source = _Bounded(source, self)
        return response


class _Bounded:
    """A response body that raises `TimeoutError` instead of reading once the
    deadline has passed."""

    def __init__(self, source, deadline: Deadline):
        self._source = source
        self._deadline = deadline

    def read(self, amount: int) -> bytes:
        self._deadline.check()
        return self._source.read(amount)

    def close(self) -> None:
        self._source.close()
