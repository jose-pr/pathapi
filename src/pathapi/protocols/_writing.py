"""Streaming one file into a target without destroying the file it replaces."""

from __future__ import annotations

import pathlib as _pathlib
import typing as _ty

from ._support import supports
from .io import _ASK_STAT, BinaryOpen
from .tree import Remove

if _ty.TYPE_CHECKING:
    from ..path import Path


def _stream(src, input, target, progress, total_size) -> None:
    """Copy `input` into `target.open("wb")`. A target that is left half
    written is removed, unless its stream commits on close: that one drops
    what it holds when the block raises and leaves what was there before."""
    created = False
    discarding = False
    try:
        with target.open("wb") as output:
            created = True
            discarding = hasattr(output, "discard")
            _fill(src, input, output, progress, total_size)
    except BaseException:
        if created and not discarding and supports(target, Remove):
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def _fill(src, input, output, progress, total_size) -> None:
    BinaryOpen._copy_stream(
        src,
        input,
        output,
        progress=(
            None
            if progress is None
            else lambda copied, total: progress(src, copied, total)
        ),
        total_size=total_size,
    )


def _stage(src, input, temp, progress, total_size) -> bool:
    """Write `input` to `temp` when `temp` takes writes in place. False, with
    nothing written, for a stream that commits on close: that one needs no
    staging, since a failed write leaves the target as it was."""
    with temp.open("wb") as output:
        if hasattr(output, "discard"):
            output.discard()
            return False
        _fill(src, input, output, progress, total_size)
    return True


def _write_target(
    src, input, target: "Path", total_size, *, progress, replacing: bool, renames: bool
) -> None:
    """Write `input` to `target`. An existing target is not written in place
    unless its stream commits on close: the new content is finished in a
    temporary sibling first, and then renamed over the old file or, where the
    class cannot rename, copied over it."""
    if not replacing:
        _stream(src, input, target, progress, total_size)
        return
    from ..sync import _replace as swap  # `sync` imports this package

    temp = swap._temp_sibling(target)
    swap._LIVE_TEMPS.add(str(temp))
    try:
        if renames:
            _stream(src, input, temp, progress, total_size)
            _swap_in(temp, target, swap)
        elif _stage(src, input, temp, progress, total_size):
            _copy_back(temp, target, swap)
        else:
            _stream(src, input, target, progress, total_size)
    except BaseException as error:
        # Once the previous version is gone the temporary file is the only
        # copy of the new one: it stays.
        if not getattr(error, swap._ONLY_COPY_ATTR, False):
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        raise
    finally:
        swap._LIVE_TEMPS.discard(str(temp))


def _swap_in(temp: "Path", target: "Path", swap) -> None:
    """Rename the finished `temp` over `target`; where that is refused
    (another filesystem, a rename that does not replace) write it over
    instead. `os.replace()` for a `pathlib.Path`: atomic."""
    try:
        if isinstance(temp, _pathlib.Path):
            temp.replace(target)
        else:
            temp.rename(target)
    except (OSError, NotImplementedError):
        _copy_back(temp, target, swap)


def _copy_back(temp: "Path", target: "Path", swap) -> None:
    """Write the finished `temp` over `target` and remove it. The only step
    that touches the old file; if it fails `temp` is kept and named."""
    try:
        with temp.open("rb") as finished:
            _stream(temp, finished, target, None, _ASK_STAT)
    except BaseException as error:
        kept = swap._only_copy(error, temp, target)
        if kept is error:
            raise
        raise kept from error
    try:
        temp.unlink(missing_ok=True)
    except Exception:
        pass
