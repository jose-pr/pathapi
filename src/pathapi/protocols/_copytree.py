"""The recursive part of `copy()`: a directory and what is listed in it."""

from __future__ import annotations

import errno as _errno
import typing as _ty

from .. import _names
from ._relation import contains
from ._removal import _write_batch_of
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
        children = list(src.iterdir())
        if not target_absent and target.exists():
            if not target.is_dir():
                raise FileExistsError(target)
            if not overwrite:
                raise FileExistsError(target)
        else:
            target.mkdir()
        windows_target = _names.is_windows_flavoured(target)
        for child in children:
            try:
                # A listed name is untrusted: one that is not a single component inside
                # `target` is refused (see the child-name paragraph of the docstring).
                _require_safe_child(target, child.name, windows_target)
                child.copy(
                    target / child.name,
                    overwrite=overwrite,
                    follow_symlinks=follow_symlinks,
                    preserve_metadata=preserve_metadata,
                    recursive=True,
                    ignore_error=ignore_error,
                    progress=progress,
                    # Passed on only when given, so a `copy()` override
                    # without the keyword still works as a child.
                    **({} if offering is None else {"on_error": offering}),
                )
            except Exception as e:
                if offering is not None:
                    if not offering(e, child):
                        raise
                elif callable(ignore_error):
                    # A notify-and-suppress hook: its return value is not
                    # consulted, so `errors.append` works.
                    ignore_error(e)
                elif not ignore_error:
                    raise
        if preserve_metadata:
            # After the children: a read-only directory could not take
            # them, and its mode is only settled once they are in.
            _copy_mode(src, target, follow_symlinks, src_stat)
