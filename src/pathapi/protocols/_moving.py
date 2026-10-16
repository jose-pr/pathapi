"""Putting a moved path where an existing one stands, without losing the
existing one before the new one is in place."""

from __future__ import annotations

import errno as _errno
import typing as _ty

from ._support import supports

if _ty.TYPE_CHECKING:
    from ..path import Path


def _swap():
    # `sync` imports this package, so it is imported when first needed.
    from ..sync import _replace as swap

    return swap


def _rename_over(src: "Path", target: "Path", *, directory: bool):
    """Rename `src` to the existing `target`, which keeps standing until `src`
    has taken its place. A file is renamed over first, as a backend with
    POSIX rename semantics replaces it atomically; a refusal sends it, like a
    directory, through `_displacing()`. `NotImplementedError` and `EXDEV`
    are raised as `rename()` raised them, with `target` untouched."""
    refusal = None
    if not directory:
        try:
            return src.rename(target)
        except OSError as error:
            if error.errno == _errno.EXDEV:
                raise
            refusal = error
    return _displacing(src, target, refusal)


def _displacing(src: "Path", target: "Path", refusal: "OSError | None"):
    """Move `target` aside, rename `src` to `target` and remove what was moved
    aside. If the second rename fails `target` is put back. `refusal`, the
    error of the plain rename that led here, is what is raised when
    `target` cannot be moved aside either."""
    swap = _swap()
    aside = swap._temp_sibling(target)
    swap._LIVE_TEMPS.add(str(aside))
    try:
        try:
            target.rename(aside)
        except OSError:
            if refusal is None:
                raise
            raise refusal from None
        try:
            moved = src.rename(target)
        except BaseException as error:
            _put_back(aside, target, error)
            raise
        _discard(aside, target)
        return moved
    finally:
        swap._LIVE_TEMPS.discard(str(aside))


def _put_back(aside: "Path", target: "Path", cause: BaseException) -> None:
    try:
        aside.rename(target)
    except BaseException as error:
        raise OSError(
            f"{target} was moved to {aside} to make room and could not be put "
            f"back: {error}"
        ) from cause


def _discard(aside: "Path", target: "Path") -> None:
    try:
        if aside.is_dir() and not aside.is_symlink():
            aside.rm(recursive=True)
        else:
            aside.unlink(missing_ok=True)
    except Exception as error:
        raise OSError(
            f"the move is done, but the replaced {target} could not be "
            f"removed and stays at {aside}: {error}"
        ) from error


def _replace_tree(src: "Path", target: "Path", handing, links: bool) -> None:
    """Copy the directory `src` over the existing directory `target`: the
    new tree is finished beside it first, so a copy that fails leaves
    `target` as it was, and is then swapped in (renamed where `target`'s class
    renames, otherwise copied over after `target` is removed)."""
    swap = _swap()
    temp = swap._temp_sibling(target)
    swap._LIVE_TEMPS.add(str(temp))
    try:
        try:
            with handing(temp):
                src.copy(temp, recursive=True, follow_symlinks=not links)
        except BaseException:
            _quietly_remove(temp)
            raise
        if _renames(target):
            try:
                _displacing(temp, target, None)
                return
            except NotImplementedError:
                pass
            except BaseException:
                _quietly_remove(temp)
                raise
        target.rm(recursive=True)
        try:
            temp.copy(target, recursive=True, follow_symlinks=not links)
        except BaseException as error:
            kept = swap._only_copy(error, temp, target)
            if kept is error:
                raise
            raise kept from error
        _quietly_remove(temp)
    finally:
        swap._LIVE_TEMPS.discard(str(temp))


def _renames(path: "Path") -> bool:
    from .transfer import Move  # `transfer` imports this module

    return supports(path, Move)


def _quietly_remove(path: "Path") -> None:
    try:
        path.rm(recursive=True, missing_ok=True)
    except Exception:
        pass
