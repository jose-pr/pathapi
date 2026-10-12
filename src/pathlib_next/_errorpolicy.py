"""The error and follow policies an operation takes as arguments."""

from __future__ import annotations

import typing as _ty

__all__ = [
    "OfferedOnce",
    "as_error_handler",
    "as_error_predicate",
    "check_follow",
    "copy_error_predicate",
]


def as_error_handler(
    ignore_error: _ty.Union[bool, _ty.Callable[..., bool], None],
    *,
    default: bool = False,
) -> _ty.Callable[..., bool]:
    """Normalize an `ignore_error` argument into a *callable* error policy.

    Every `ignore_error` (and `copy()`'s `on_error`) parameter in this library
    accepts either a bool or a callable, but the callables have **different
    arities** per call site (`Path.rm()` and `Path.copy(on_error=)` ->
    `(error, path)`, `Path.copy(ignore_error=)` -> `(error)`,
    `PathSyncer.sync()` -> `(error, source, target, event)`). Changing the
    arity of an existing keyword would break its callers, so this helper only
    normalizes the *bool* case and passes a supplied callable through
    untouched -- it is invoked with whatever arguments its own call site
    already uses.

    `None` means "no policy supplied": it resolves to `default` (False for
    every current caller, i.e. raise on the first error), which preserves
    `Path.copy(ignore_error=None)`'s documented meaning.

    Centralizing this keeps a fourth call site from drifting back into
    calling a bool (see `PathSyncer.sync()`'s symlink branch, which did
    exactly that and raised `TypeError: 'bool' object is not callable`).
    """
    if callable(ignore_error):
        return ignore_error
    if ignore_error is None:
        ignore_error = default
    result = bool(ignore_error)
    return lambda *args, **kwargs: result


class OfferedOnce:
    """An `(error, path) -> bool` error predicate that sees each error once.

    `True` swallows the error. An error it answered `False` for, and an
    exception it raised itself, are not offered again when they travel up
    through the enclosing directories of a recursive operation: they are
    raised as they are. Build one with `as_error_predicate()`.
    """

    __slots__ = ("_predicate", "_seen")

    def __init__(self, predicate: _ty.Callable[[Exception, _ty.Any], bool]) -> None:
        self._predicate = predicate
        self._seen: "list[BaseException]" = []

    def __call__(self, error: Exception, path: _ty.Any) -> bool:
        if any(error is seen for seen in self._seen):
            return False
        try:
            swallow = self._predicate(error, path)
        except BaseException as raised:
            self._seen.append(raised)
            raise
        if swallow:
            return True
        self._seen.append(error)
        return False


def as_error_predicate(
    on_error: _ty.Union[bool, _ty.Callable[[Exception, _ty.Any], bool], None],
) -> OfferedOnce:
    """`on_error` as an `OfferedOnce` predicate: a bool becomes a constant
    answer (`None` is `False`), a callable is called as `(error, path)`, and
    an `OfferedOnce` is returned as it is, so a recursive call that is handed
    the predicate of its caller shares what that predicate has seen."""
    if isinstance(on_error, OfferedOnce):
        return on_error
    return OfferedOnce(as_error_handler(on_error))


def check_follow(name: str, policy) -> None:
    """Raise `ValueError` naming `name` unless `policy` is `True`, `False`,
    `None` or a callable: what `rm()` accepts for `follow_symlinks=` and
    `follow_binds=`. An `rm()` override calls it before removing anything."""
    if not (policy is None or callable(policy) or isinstance(policy, bool)):
        raise ValueError(f"{name} must be True, False, None or a callable")


def copy_error_predicate(ignore_error, on_error) -> "OfferedOnce | None":
    """The `(error, path) -> bool` predicate for `copy()`'s `on_error=`, or
    None when it was not given. Raises `TypeError` when `ignore_error=` was
    given too: a `copy()` override calls this before it copies anything."""
    if on_error is None:
        return None
    if ignore_error is not None:
        raise TypeError("copy() takes ignore_error or on_error, not both")
    return as_error_predicate(on_error)
