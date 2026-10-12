"""How a target stands to a source: the same entry, another name, outside or inside."""

from __future__ import annotations

import errno as _errno
import os as _os
import pathlib as _pathlib
import typing as _ty

if _ty.TYPE_CHECKING:
    from ._path import Path, _Held


# How `move()` and `copy()` find a target standing to the source (`_relation`).
_SAME = "same"  # one directory entry, spelled differently
_OTHER = "other"  # another name of the same file (a hard link, a link to it)
_OUTER = "outer"  # the target is a directory that holds the source
_INNER = "inner"  # the target lies inside the source


def _local_file(path) -> "_pathlib.Path | None":
    """The `pathlib.Path` through which `path` is a file of this machine, or
    None: a `LocalPath`, a stdlib path, or a path holding one as `filepath`
    (a `file:` URI)."""
    if isinstance(path, _pathlib.Path):
        return path
    try:
        local = getattr(path, "filepath", None)
    except Exception:
        return None
    return local if isinstance(local, _pathlib.Path) else None


def _location(path: "_pathlib.Path", follow: bool) -> str:
    """Where `path` points: absolute, with every symlink of its directory part
    resolved and, when `follow`, the final component's too."""
    text = _os.path.join(_os.getcwd(), _os.fspath(path))
    head, tail = _os.path.split(text)
    if follow or tail in ("", ".", ".."):
        return _os.path.realpath(text)
    return _os.path.join(_os.path.realpath(head), tail)


def _inside(outer: str, inner: str) -> bool:
    """Whether `inner` lies strictly below `outer` (both `normcase`d)."""
    if outer == inner:
        return False
    try:
        return _os.path.commonpath([outer, inner]) == outer
    except ValueError:
        return False


def _is_one_entry(mine: str, theirs: str) -> bool:
    """Whether two locations of one file are a single directory entry under
    two spellings (a case or short-name alias) rather than two names of it
    (hard links, which a directory lists both). Unsure counts as one entry:
    that removes nothing."""
    (head, name), (other_head, other_name) = (
        _os.path.split(mine),
        _os.path.split(theirs),
    )
    try:
        if not _os.path.samefile(head, other_head):
            return False
        listed = set(_os.listdir(head))
    except OSError:
        return True
    return not (name != other_name and name in listed and other_name in listed)


def _local_relation(mine, theirs, follow: bool) -> "str | None":
    """`_relation()` for two files of this machine, decided by where each
    really is and, failing that, by their identity (`os.path.samefile`)."""
    try:
        at, there = _location(mine, follow), _location(theirs, follow)
        normal, other = _os.path.normcase(at), _os.path.normcase(there)
        if normal == other:
            return _SAME
        if _inside(other, normal):
            return _OUTER
        if _inside(normal, other):
            return _INNER
        if not _os.path.samefile(mine, theirs):
            return None
    except (OSError, ValueError):
        return None
    return _SAME if _is_one_entry(at, there) else _OTHER


def _relation(
    src: Path,
    target: Path,
    *,
    follow: bool,
    identity: bool = True,
    held: "_Held | None" = None,
) -> "str | None":
    """How `target` stands to `src`: `_SAME`, `_OTHER`, `_OUTER`, `_INNER`,
    or None when they are unrelated or this cannot tell.

    Two files of this machine are compared by where they really are, whatever
    their classes; a type that answers `_node_key()` is compared by that;
    anything else only between two paths of one type that
    `_same_filesystem()` places together, by `samefile()` where `stat()`
    carries `st_dev`/`st_ino` (when `identity`; it costs a stat of each) and
    by `==` otherwise. `follow` says whether a final symlink is looked
    through (a copy reads it) or is the entry itself (a move renames it).
    `held` is the stats of `src` and `target` the caller already took, so
    `samefile()` does not take them again."""
    mine, theirs = _local_file(src), _local_file(target)
    if mine is not None and theirs is not None:
        return _local_relation(mine, theirs, follow)
    try:
        keys = (src._node_key(), target._node_key())
    except Exception:
        keys = (None, None)
    if None not in keys:
        (space, names), (other_space, other_names) = keys
        if space is not other_space:
            return None
        if names == other_names:
            return _SAME
        if names[: len(other_names)] == other_names:
            return _OUTER
        if other_names[: len(names)] == names:
            return _INNER
        return None
    if type(src) is not type(target):
        return None
    if identity:
        try:
            if src.samefile(target) if held is None else held.same_file(src, target):
                return _SAME
        except (NotImplementedError, OSError, TypeError, ValueError):
            pass
    if not src._same_filesystem(target):
        return None
    try:
        if src == target:
            return _SAME
        if target.is_relative_to(src):
            return _INNER
        if src.is_relative_to(target):
            return _OUTER
    except Exception:
        pass
    return None


def contains(src: Path, target: Path) -> bool:
    """Whether `target` is `src` or lies inside it (pathlib 3.14's copy()
    refuses both), looking through symlinks. Paths `_same_filesystem()`
    reports in different namespaces are never nested."""
    return _relation(src, target, follow=True, identity=False) in (
        _SAME,
        _OTHER,
        _INNER,
    )


def _same_file(src: Path, target: Path, *, identity: bool = True) -> bool:
    """Whether `src` and `target` name the same file, looking through
    symlinks (see `_relation`, and its `identity`)."""
    mine, theirs = _local_file(src), _local_file(target)
    if mine is not None and theirs is not None:
        # The cheap form of `_local_relation()`: copy() asks this once per
        # file, and only identity matters to it.
        try:
            return _os.path.samefile(mine, theirs)
        except (OSError, ValueError):
            pass
        try:
            return _os.path.normcase(_os.path.abspath(mine)) == _os.path.normcase(
                _os.path.abspath(theirs)
            )
        except (OSError, ValueError):
            return False
    return _relation(src, target, follow=True, identity=identity) in (_SAME, _OTHER)


def _drop_second_name(src: Path, src_stat, target: Path, target_stat) -> Path:
    """`move()` between two names of one file: the target name already holds
    the content, so only the source name goes. A link to the file, or a
    directory reachable twice, is refused: removing either name would leave
    the other pointing at nothing."""
    if src_stat.is_symlink() or target_stat.is_symlink() or src_stat.is_dir():
        raise OSError(_errno.EINVAL, "Source and target are the same file", str(target))
    src.unlink()
    return target
