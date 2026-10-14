"""Listing a directory for the glob engine, and telling a directory from a link."""

from __future__ import annotations

import os as _os
import pathlib as _pathlib
import typing as _ty

if _ty.TYPE_CHECKING:
    from ..path import P as _Globable
else:

    class _Globable(_ty.Protocol): ...


def _report_unlistable(path: _Globable, on_error) -> None:
    """Tell `on_error` why `path` has nothing to select from (a missing path,
    a file), in the words listing it would use."""
    if on_error is not None:
        _scan(path, on_error)


def _scan(directory: _Globable, on_error=None):
    """(child, non-following stat or None) per entry; nothing if listing
    fails. Materialized so a lazily-raising listing is still caught here.

    `on_error`, when given, is called as `on_error(error)` with the failure
    -- the same contract as `Path.walk(on_error=)` and `os.walk`. Raising
    from it propagates, which is how a caller turns an unreadable directory
    back into an error; returning treats it as "nothing here", which is what
    happens with no hook at all. `error.filename` is filled in with the
    directory when the backend left it empty, so one argument is enough to
    say where.
    """
    scandir = getattr(directory, "_scandir", None)
    try:
        if scandir is None:
            return [(child, None) for child in directory.iterdir()]
        return [(_child(directory, name, stat), stat) for name, stat in scandir()]
    except OSError as error:
        if on_error is not None:
            if not getattr(error, "filename", None):
                try:
                    error.filename = str(directory)
                except Exception:
                    pass
            on_error(error)
        return ()


def _identity(path: _Globable) -> "tuple | None":
    """`(st_dev, st_ino)` of what `path` resolves to, or None when the
    backend cannot say. Follows links deliberately: a Windows junction
    reports `is_symlink() == False`, so its own metadata is no help --
    `stat()` returning the TARGET's identity is what makes a loop visible.
    """
    try:
        st = path.stat()
    except (OSError, NotImplementedError, ValueError):
        return None
    dev = getattr(st, "st_dev", None)
    ino = getattr(st, "st_ino", None)
    # `st_ino` is zero where the filesystem reports no file identity; every
    # directory would then share one key and look like its own ancestor.
    if dev is None or not ino:
        return None
    return (dev, ino)


def _child(directory: _Globable, name: str, stat=None) -> _Globable:
    """The child of `directory` called exactly `name`, a name its listing
    returned. A caller that has not checked it with `providers.is_safe_child_name()`
    must, since a URI path joined with `/` resolves `..` and splits at `/`."""
    if getattr(directory, "_pop_stat_hint", None) is not None:
        # A URI path: the name is attached as one segment, and seeding the
        # listing's stat saves a round trip per entry.
        return directory._make_child_relpath(name, stat_hint=stat)
    return directory / name


def _names_a_directory(path: _Globable, lexists: bool) -> bool:
    """Whether the literal `path`, given with a trailing separator, is a
    directory. `lexists` is pathlib 3.13+'s test (see
    `_DIRONLY_LITERAL_IS_LEXISTS`), made on the file `path` is; any other path
    is a directory when it follows to one."""
    if lexists and isinstance(path, _pathlib.Path):
        return _os.path.lexists(_os.fspath(path) + _os.sep)
    return path.is_dir()


def _entry_is_dir(path: _Globable, stat, *, follow_symlinks: bool) -> bool:
    if stat is not None and not (follow_symlinks and stat.is_symlink()):
        return stat.is_dir()
    if follow_symlinks:
        return path.is_dir()
    return path.is_dir() and not path.is_symlink()
