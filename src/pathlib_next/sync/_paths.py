"""Questions about the paths of a sync: junctions, mounts, names, overlap."""

from __future__ import annotations

from ..path import Path, _relation


def _is_junction(path: Path) -> bool:
    """Whether `path` is a directory junction: a link to another directory
    that lists as an ordinary one. A mount point is not one, even where the
    platform builds both from the same reparse point: it is a filesystem
    boundary inside the tree, and a sync writes into it."""
    try:
        if not path.is_junction():
            return False
    except (NotImplementedError, OSError, ValueError):
        return False
    try:
        return not path.is_mount()
    except (NotImplementedError, OSError, ValueError):
        return True


def _is_mount(path: Path) -> bool:
    """Whether `path` is a mount point (a mounted volume, a bind mount); a
    class that cannot answer says no."""
    try:
        return bool(path.is_mount())
    except (NotImplementedError, OSError, ValueError):
        return False


def _child_name(path: Path) -> str:
    # A directory child of a `UriPath` can end in "/", leaving `.name`
    # empty; its last real component is then the parent's name.
    return path.name or path.parent.name


def _paths_overlap(source: Path, target: Path) -> bool:
    """Whether `source` and `target` are the same tree, one contains the
    other, or they are two names of one file. Decided by `path._relation()`:
    two files of this machine by where each really is (a symlink cannot hide
    the overlap), whatever their classes; a type answering `_node_key()`
    (`MemPath`, archive members) by the node, across classes and spellings;
    anything else only between two paths of one type that
    `_same_filesystem()` places together, so two `MemPath` trees on separate
    backends, or two hosts behind one path type, do not overlap even with
    equal segments."""
    return _relation(source, target, follow=True, identity=False) is not None
