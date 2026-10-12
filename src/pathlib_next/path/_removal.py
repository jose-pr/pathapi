"""Helpers of listing and removal: the child listing, junctions and follow policies."""

from __future__ import annotations

import contextlib as _contextlib
import typing as _ty

from .._filestat import FileStat

if _ty.TYPE_CHECKING:
    from ._path import Path


def _write_batch_of(path) -> "_ty.ContextManager[object]":
    """`path._write_batch()`, where a path without the hook (a stdlib path)
    gets a block that does nothing."""
    hook = getattr(path, "_write_batch", None)
    return hook() if callable(hook) else _contextlib.nullcontext()


def _is_junction(path: "Path") -> bool:
    """`path.is_junction()`, where a class that cannot answer says no."""
    try:
        return bool(path.is_junction())
    except (NotImplementedError, OSError, ValueError):
        return False


def _follow_policy(policy, path: "Path") -> str:
    """Resolve a `follow_symlinks=`/`follow_binds=` value for `path`.

    `False` (the default) means "remove the entry itself, never what is
    behind it" -- `rm -r` semantics; `True` means "remove what is behind
    it"; `None` means "leave it alone entirely". A callable is asked per
    entry (`policy(path)`) and returns one of those three, so one tree can
    keep one mount and follow another.

    Returns the internal name: "rm", "follow" or "ignore". An answer that is
    none of the three raises `_InvalidPolicy`, which `rm()` never hands to
    its `ignore_error`.
    """
    if callable(policy):
        policy = policy(path)
    if policy is None:
        return "ignore"
    if policy is True:
        return "follow"
    if policy is False:
        return "rm"
    raise _InvalidPolicy(f"expected True, False, None or a callable, got {policy!r}")


class _InvalidPolicy(ValueError):
    """A follow policy that is not `True`, `False` or `None`."""


def _listing(path: Path) -> "_ty.Iterator[_ty.Tuple[str, _ty.Optional[FileStat]]]":
    """`path._scandir()` as `(name, non-following stat or None)` pairs. An
    entry that has `name` and `stat()`, as `os.scandir()` yields, is adapted;
    `_scandir()` is documented to yield pairs."""
    for entry in path._scandir():
        if isinstance(entry, tuple) and len(entry) == 2:
            yield entry
            continue
        try:
            stat = FileStat.from_stat(entry.stat(follow_symlinks=False))
        except OSError:
            stat = None
        yield entry.name, stat
