"""The recursive walk behind "**": loop and symlink guards."""

from __future__ import annotations

import typing as _ty

from ._entries import _Globable, _entry_is_dir, _identity, _scan

#: Symlinks one descent may pass through when `recurse_symlinks=True` and the
#: backend's stat carries no identity to tell a loop by: what the operating
#: system allows in resolving one path.
_MAX_LINK_TRAVERSALS = 40


def _recurse(
    top: _Globable,
    include_hidden: bool,
    with_files: bool,
    on_error=None,
    bound_loops: bool = False,
    recurse_symlinks: bool = False,
) -> _ty.Iterator[_Globable]:
    """Yield `top` and every directory below it (plus every other entry when
    `with_files`), never descending through a directory symlink unless
    `recurse_symlinks` (see `_follow_guard()`).

    With `bound_loops`, a directory whose identity (`st_dev`, `st_ino`) is
    already on the CURRENT DESCENT PATH is skipped: that is what a loop is
    -- a directory reachable below itself -- and it bounds a Windows
    junction loop, which no symlink check can see (a junction reports
    `is_symlink() == False`).

    The ancestor chain is the whole rule, not a set of everything seen. One
    directory deliberately reachable under two SIBLING names (a shared
    config layer junctioned in as `site-a` and `site-b`) is not a loop: the
    walk terminates, and both names must expand. A `visited` set spanning
    the traversal dropped the second one silently. `find -L` draws the line
    in the same place.

    A backend whose stat has no identity cannot be bounded this way and is
    walked unbounded.
    """
    yield top
    top_key = _identity(top) if bound_loops else None
    # Each stack entry carries the identities of the directories the walk is
    # currently inside -- pushed on descent, dropped with the branch -- and,
    # for `recurse_symlinks`, the directories themselves and the links passed.
    stack = [(top, (top_key,) if top_key is not None else (), (top,), 0)]
    while stack:
        directory, ancestors, chain, links = stack.pop()
        for child, stat in _scan(directory, on_error):
            if not include_hidden and child.is_hidden():
                continue
            is_dir = _entry_is_dir(child, stat, follow_symlinks=recurse_symlinks)
            child_ancestors, child_chain, child_links = ancestors, chain, links
            enter = is_dir
            if is_dir:
                if recurse_symlinks:
                    # A link that would loop is still an entry, but not entered.
                    enter, child_chain, child_links = _follow_guard(
                        child, stat, chain, links
                    )
                if enter:
                    descend, child_ancestors = _loop_guard(
                        child, ancestors, bound_loops
                    )
                    if not descend:
                        continue
            if is_dir or with_files:
                yield child
            if enter:
                stack.append((child, child_ancestors, child_chain, child_links))


def _recurse_dirs(
    top: _Globable,
    include_hidden: bool,
    on_error=None,
    bound_loops: bool = False,
    recurse_symlinks: bool = False,
) -> _ty.Iterator[_ty.Tuple[_Globable, _ty.Sequence]]:
    """`_recurse()` over directories only, as `(directory, its listing)`: the
    caller lists each one next, and gets the listing that found the
    directories below it instead of asking the backend again. Same order,
    same loop rule. A directory waiting its turn keeps its subdirectories,
    not its whole listing."""
    entries = _scan(top, on_error)
    yield top, entries
    top_key = _identity(top) if bound_loops else None
    stack = [
        (
            _subdirectories(entries, include_hidden, recurse_symlinks),
            (top_key,) if top_key is not None else (),
            (top,),
            0,
        )
    ]
    while stack:
        children, ancestors, chain, links = stack.pop()
        for child, stat in children:
            child_chain, child_links = chain, links
            if recurse_symlinks:
                descend, child_chain, child_links = _follow_guard(
                    child, stat, chain, links
                )
                if not descend:
                    continue
            descend, child_ancestors = _loop_guard(child, ancestors, bound_loops)
            if not descend:
                continue
            entries = _scan(child, on_error)
            yield child, entries
            stack.append(
                (
                    _subdirectories(entries, include_hidden, recurse_symlinks),
                    child_ancestors,
                    child_chain,
                    child_links,
                )
            )


def _subdirectories(
    entries, include_hidden: bool, recurse_symlinks: bool = False
) -> "list[tuple[_Globable, _ty.Any]]":
    """`(child, its listing stat)` for each directory among `entries`; through
    a symlink to a directory only when `recurse_symlinks`."""
    return [
        (child, stat)
        for child, stat in entries
        if (include_hidden or not child.is_hidden())
        and _entry_is_dir(child, stat, follow_symlinks=recurse_symlinks)
    ]


def _follow_guard(child: _Globable, stat, chain: tuple, links: int):
    """`(descend, chain, links)` for a directory `child` of the last
    directory of `chain` (the directories the descent is inside, the top
    first), `links` of which were entered through a symlink.

    A directory reached through a symlink is not entered when it is one of
    the directories already in `chain` -- a loop, which would match everything
    under it again and again. Where the stat has no identity (most remote
    backends) a link is not entered after 40 of them. A directory reached
    without a link cannot close a loop by itself and costs no stat."""
    if not _is_link(child, stat):
        return True, chain + (child,), links
    key = _identity(child)
    if key is not None:
        if any(_identity(directory) == key for directory in chain):
            return False, chain, links
    elif links >= _MAX_LINK_TRAVERSALS:
        return False, chain, links
    return True, chain + (child,), links + 1


def _is_link(path: _Globable, stat) -> bool:
    """Whether `path` is a symlink, by the listing's non-following stat when
    there is one."""
    if stat is not None:
        return stat.is_symlink()
    try:
        return path.is_symlink()
    except (OSError, NotImplementedError, ValueError):
        return False


def _loop_guard(child: _Globable, ancestors: tuple, bound_loops: bool):
    """`(descend, identities of the directories child is inside)`. A child
    that is one of its own ancestors is neither descended into nor yielded:
    the next pattern component would match everything under it a second
    time, through the loop."""
    if not bound_loops:
        return True, ancestors
    key = _identity(child)
    if key is None:
        return True, ancestors
    if key in ancestors:
        return False, ancestors
    return True, ancestors + (key,)
