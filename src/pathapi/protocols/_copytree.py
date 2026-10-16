"""The recursive part of `copy()`: a directory and what is listed in it."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import typing as _ty

from .. import _fanout, _names
from ..glob import _entries
from ._relation import contains
from ._removal import _listing, _write_batch_of
from ._transfer import _copy_mode, _require_safe_child

if _ty.TYPE_CHECKING:
    from ..path import Path


def _copy_tree(
    src: "Path",
    target: "Path",
    src_stat,
    target_absent: bool,
    *,
    overwrite,
    follow_symlinks,
    preserve_metadata,
    ignore_error,
    progress,
    offering,
) -> None:
    """Copy the directory `src` and what it holds to `target`, each child
    through its own `copy(recursive=True)`; `copy()` passes its keywords on."""
    if contains(src, target):
        # Checked before anything is created: the new directory would
        # be listed and copied into itself without end.
        raise OSError(
            _errno.EINVAL,
            "Cannot copy a directory into itself",
            str(target),
        )
    # The writes below may be committed together by a backend that
    # pays for each one (see `_write_batch`).
    with _write_batch_of(target):
        # Listed before the target exists, as shutil.copytree does.
        fan = _fanout.ACTIVE.get()
        fanned = fan is not None and fan.owned()
        children_listing = list(_listing(src)) if fanned else None
        children = [] if fanned else list(src.iterdir())
        if not target_absent and target.exists():
            if not target.is_dir():
                raise FileExistsError(target)
            if not overwrite:
                raise FileExistsError(target)
        else:
            target.mkdir()
        windows_target = _names.is_windows_flavoured(target)
        copy = _ChildCopy(
            target,
            windows_target,
            overwrite=overwrite,
            follow_symlinks=follow_symlinks,
            preserve_metadata=preserve_metadata,
            ignore_error=ignore_error,
            progress=progress,
            offering=offering,
        )
        if fanned:
            _copy_children_fanned(fan, src, children_listing, copy)
        else:
            for child in children:
                copy(child)
        if preserve_metadata:
            # After the children: a read-only directory could not take
            # them, and its mode is only settled once they are in.
            _copy_mode(src, target, follow_symlinks, src_stat)


def _copy_tree_pooled(workers: int, *args, progress, **keywords) -> None:
    """`_copy_tree()` with the files of each directory copied by up to
    `workers` threads. `progress` is then called one call at a time."""
    with _fanout.Fanout(workers) as fan:
        token = _fanout.ACTIVE.set(fan)
        try:
            _copy_tree(
                *args,
                progress=None if progress is None else _serialized(fan, progress),
                **keywords,
            )
        finally:
            _fanout.ACTIVE.reset(token)
        fan.check()


def _serialized(fan, callback):
    def call(*args):
        with fan.lock:
            return callback(*args)

    return call


class _ChildCopy:
    """The copy of one listed child into `target`, with the error policy of
    the enclosing `copy()`: what the loop of a serial copy does per child."""

    def __init__(self, target, windows, *, offering, ignore_error, **keywords):
        self.target = target
        self.windows = windows
        self.offering = offering
        self.ignore_error = ignore_error
        self.keywords = keywords

    def __call__(self, child, name=None, lock=None) -> None:
        try:
            # A listed name is untrusted: one that is not a single component inside
            # `target` is refused (see the child-name paragraph of the docstring).
            name = child.name if name is None else name
            _require_safe_child(self.target, name, self.windows)
            child.copy(
                self.target / name,
                recursive=True,
                ignore_error=self.ignore_error,
                # Passed on only when given, so a `copy()` override
                # without the keyword still works as a child.
                **({} if self.offering is None else {"on_error": self.offering}),
                **self.keywords,
            )
        except Exception as e:
            with lock if lock is not None else _contextlib.nullcontext():
                self._decline(e, child)

    def _decline(self, e, child) -> None:
        if self.offering is not None:
            if not self.offering(e, child):
                raise
        elif callable(self.ignore_error):
            # A notify-and-suppress hook: its return value is not
            # consulted, so `errors.append` works.
            self.ignore_error(e)
        elif not self.ignore_error:
            raise


def _copy_children_fanned(fan, src, listing, copy) -> None:
    """Copy the listed children: a directory on this thread, so that its own
    files join the pool, and every other entry on a pool thread. Returns when
    all are done; the first error that ends the copy is raised."""
    with fan.batch() as batch:
        for name, stat in listing:
            fan.check()
            child = (
                _entries._child(src, name, stat)
                if _names.is_safe_child_name(name)
                else src
            )
            if stat is None or stat.is_dir() or child is src:
                copy(child, name, fan.lock)
            else:
                batch.submit(copy, child, name, fan.lock)
