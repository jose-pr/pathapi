"""Whether a source and a target already agree: checksum and modification time."""

from __future__ import annotations

import math as _math

from ..path import Path
from .. import checksum as _checksum
from ._events import PathAndStat

#: `PathSyncer`'s own default checksum policy (native digest preferred, streaming
#: fallback), which `sync()` special-cases (`_default_checksums_match()`); a
#: caller-supplied `checksum` is called as given, one path in, value out, with `==`.
_DEFAULT_ALGORITHM = "md5"


def _default_checksum(entry: "PathAndStat") -> str:
    # The sentinel that marks the default policy: `sync()` recognizes it and
    # calls `_default_checksums_match()` instead. It is also the plain
    # `Callable[[PathAndStat], Any]` that `PathSyncer().checksum` reads as.
    native = _checksum.native(entry.path, _DEFAULT_ALGORITHM)
    if native is not None:
        return native
    return _checksum.stream(entry.path, _DEFAULT_ALGORITHM)


def _shared_native_algorithm(source: Path, target: Path) -> "str | None":
    """Pick an algorithm both sides advertise via
    `NativeChecksum.supported_checksums()` (see `protocols/checksum.py`),
    preferring `_DEFAULT_ALGORITHM` ("md5") when both sides support it,
    `PathSyncer`'s default algorithm. Returns `None` if either side has no
    `supported_checksums` at all (a `NativeChecksum` implementation isn't
    required to override the advisory method -- the base default already
    returns `frozenset()`, indistinguishable here from "doesn't implement
    the protocol"), or if the two sides' advertised sets don't overlap.
    """
    source_supported = getattr(source, "supported_checksums", None)
    target_supported = getattr(target, "supported_checksums", None)
    if source_supported is None or target_supported is None:
        return None
    shared = source_supported() & target_supported()
    if not shared:
        return None
    if _DEFAULT_ALGORITHM in shared:
        return _DEFAULT_ALGORITHM
    return next(iter(shared))


def _default_checksums_match(source: "PathAndStat", target: "PathAndStat") -> bool:
    """`PathSyncer`'s default in-sync check: prefer each side's
    `NativeChecksum.checksum()` (no network transfer needed just to
    *decide* whether a copy is needed), but only trust a native digest from
    one side if the OTHER side can also produce a digest under the exact
    same algorithm -- native or streamed. Mixing "native digest from A" with
    "streamed digest from B" would only be numerically safe if both are
    guaranteed true content hashes under the same algorithm; the protocol
    contract for `NativeChecksum.checksum()` already guarantees that
    (`NotImplementedError` on any doubt, e.g. S3 multipart ETags), so this
    conservative both-native-or-both-streamed policy is a deliberate
    simplicity choice, not a soundness requirement -- see
    `protocols/checksum.py`.

    Algorithm selection: try `supported_checksums()` intersection first
    (`_shared_native_algorithm`) -- avoids a doomed native attempt when the
    two sides' capabilities don't overlap on `_DEFAULT_ALGORITHM`. Falls
    back to trying `_DEFAULT_ALGORITHM` directly (the pre-`supported_checksums`
    behavior) when either side doesn't implement the advisory method at
    all, since a `NativeChecksum` implementation is never required to
    override it.
    """
    # Whether each side can answer at all is known without I/O: ask for a
    # digest only when both can, and ask the second only if the first did,
    # so a digest that would be thrown away is never computed.
    if all(getattr(entry.path, "checksum", None) for entry in (source, target)):
        algorithm = (
            _shared_native_algorithm(source.path, target.path) or _DEFAULT_ALGORITHM
        )
        source_native = _checksum.native(source.path, algorithm)
        if source_native is not None:
            target_native = _checksum.native(target.path, algorithm)
            if target_native is not None:
                return source_native == target_native
    return _checksum.stream(source.path, _DEFAULT_ALGORITHM) == _checksum.stream(
        target.path, _DEFAULT_ALGORITHM
    )


def _is_local(path) -> bool:
    """`quick_check`'s locality test. `is_local()` is an optional method of a
    path whose server may be this machine or another one (a URI path, an SFTP
    path); a plain `LocalPath` or `MemPath` has no such method at all. Both of
    those ARE effectively local for this purpose (a real local filesystem, or
    an in-memory structure with no network cost either way), so "no
    `is_local()` method" is treated as local -- the safe default, since it
    only preserves the always-checksum behavior rather than skipping a
    comparison it shouldn't.

    An `is_local()` that resolves a host name can fail with `OSError` when the
    resolver itself fails (no network at all), as opposed to a name that
    resolves nowhere, which answers `False`. That is treated as "local" for
    the same safe-default reason: quick_check does not kick in and the sync
    falls through to a real checksum comparison, rather than letting an
    unrelated resolver failure crash the sync outright.
    """
    is_local = getattr(path, "is_local", None)
    if is_local is None:
        return True
    try:
        return is_local()
    except OSError:
        return True


#: Two floats of one instant differ by less than this after a filesystem has
#: stored them as nanoseconds and handed them back.
_MTIME_TICK = 1e-6


def _same_mtime(first: float, second: float) -> bool:
    """Whether two modification times name the same instant at the resolution
    of the coarser: whole seconds when either has no fractional part (SFTP,
    FTP and the object stores report seconds, a local filesystem fractions),
    else to a microsecond."""
    if first == second:
        return True
    if first % 1 == 0 or second % 1 == 0:
        return _math.floor(first) == _math.floor(second)
    return abs(first - second) < _MTIME_TICK


def _quick_check_in_sync(source: "PathAndStat", target: "PathAndStat") -> bool:
    """The rsync-style "quick check" pre-check: True only when BOTH
    `st_size` and `st_mtime` already match between the two cached stats --
    metadata `PathAndStat` already carries from listing, no extra round
    trip. Never used to conclude "out of sync" (a caller must fall through
    to a real checksum comparison on any mismatch) -- see `PathSyncer`'s
    class docstring for why.

    An `st_mtime` of 0 (or missing) on either side is "unknown", never a
    timestamp: MemPath, GitHub/GitLab, `data:` and HTTP without
    Last-Modified all report 0, and two unknowns are not a match. So is a
    stat whose listing did not report a size (`size_known` False). Times
    match as `_same_mtime()` says.
    """
    source_stat, target_stat = source.stat, target.stat
    if source_stat is None or target_stat is None:
        return False
    if not (
        getattr(source_stat, "size_known", True)
        and getattr(target_stat, "size_known", True)
    ):
        return False
    if not source_stat.st_mtime or not target_stat.st_mtime:
        return False
    return source_stat.st_size == target_stat.st_size and _same_mtime(
        source_stat.st_mtime, target_stat.st_mtime
    )
