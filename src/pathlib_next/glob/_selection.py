"""Selecting the paths a pattern names: `glob()` and `select()`."""

from __future__ import annotations

import sys as _sys
import typing as _ty

from ._entries import (
    _Globable,
    _child,
    _entry_is_dir,
    _names_a_directory,
    _report_unlistable,
    _scan,
)
from ._patterns import (
    RECURSIVE,
    WILDCARD_PATTERN,
    _PARTIAL_DOUBLESTAR_ALLOWED,
    _TRAILING_SLASH_SELECTS_DIRS,
    _collapse_recursive,
    compile_pattern,
)
from ._recursion import _recurse, _recurse_dirs

# CPython 3.13 made a trailing "**" select files as well as directories;
# earlier versions select directories only. glob() follows the running
# interpreter so LocalPath keeps matching the pathlib it runs next to.
_DOUBLESTAR_SELECTS_FILES = _sys.version_info >= (3, 13)

# How a literal component is checked; `native=False` pins both to the current pathlib's.
#: Before 3.13 a literal that is not the last component must be a directory; from
#: 3.13 it is joined unchecked and only the whole path is tested.
_INTERMEDIATE_LITERAL_IS_CHECKED = _sys.version_info < (3, 13)
#: From 3.12 the last literal is tested without following a link (a dangling symlink
#: is selected); before that, with `exists()`.
_FINAL_LITERAL_IS_NOT_FOLLOWED = _sys.version_info >= (3, 12)

#: Followed when `native=True`. 3.12 selects every literal component (but "..") from
#: the directory listing, as a wildcard: a literal typed in another case yields the
#: stored name where the filesystem ignores case. Other versions join it as typed.
_LITERALS_ARE_LISTED = _sys.version_info[:2] == (3, 12)
#: From 3.13 a final literal with a trailing separator is tested as `lexists(name +
#: os.sep)`. What that answers for a link is the platform's: Windows does not follow a
#: directory link there, macOS follows a link to a file.
_DIRONLY_LITERAL_IS_LEXISTS = _sys.version_info >= (3, 13)

#: Components that name a directory relative to the one before them.
_SPECIAL_PARTS = (".", "..")


def glob(
    path: _Globable,
    *,
    dironly: bool = False,
    root_dir: _Globable | None = None,
    recursive: bool | None = False,
    include_hidden: bool = False,
    case_sensitive: bool | None = None,
    native: bool = True,
    on_error: "_ty.Callable[[OSError], None] | None" = None,
    bound_loops: bool = False,
    recurse_symlinks: bool = False,
) -> _ty.Iterator[_Globable]:
    """Return an iterator which yields the paths matching a pathname pattern.

    `path` is the pattern itself, as a path (e.g. `UriPath("file:/x/**/*.py")`).
    The pattern may contain simple shell-style wildcards a la fnmatch. Like
    the stdlib `glob` module, and unlike `Path.glob()`, hidden entries (names
    starting with a dot) are not matched by wildcards and not descended into
    by "**" unless `include_hidden` is true.

    If recursive is true, the pattern '**' will match zero or more
    directories and subdirectories; as the last component it also matches
    files on Python 3.13+ or with `native=False`, and directories only
    otherwise. `recursive=None` decides
    from the pattern itself, as `Path.glob()` does: a "**" component enables
    it. `native=` follows the running interpreter's rules, or applies one
    rule on every version -- see `parse_pattern()`. `recurse_symlinks=True`
    lets "**" descend through symlinks to directories, see `select()`.

    The pattern is validated when this is called and the paths are selected
    lazily. The path's anchor (a drive, an extended-length drive prefix) is
    never a wildcard, whatever characters it holds.
    """
    segments = list(path.segments)
    if len(segments) > 1 and segments[-1] == "":
        segments.pop()
        if native and not _TRAILING_SLASH_SELECTS_DIRS:
            pass  # pathlib ignores a trailing separator before 3.11
        else:
            dironly = True
    if recursive is None:
        recursive = RECURSIVE in segments
    if native and not _PARTIAL_DOUBLESTAR_ALLOWED:
        for segment in segments:
            if RECURSIVE in segment and segment != RECURSIVE:
                raise ValueError(
                    "Invalid pattern: '**' can only be an entire path component"
                )
    anchor = getattr(path, "anchor", "")
    searched = 1 if segments and anchor and segments[0] == anchor else 0
    first_wildcard = next(
        (
            i
            for i, seg in enumerate(segments)
            if i >= searched and WILDCARD_PATTERN.search(seg)
        ),
        max(len(segments) - 1, 0),
    )
    base = path.with_segments(*segments[:first_wildcard])
    if root_dir is not None:
        base = root_dir / base
    parts = [part for part in segments[first_wildcard:] if part not in ("", ".")]
    return _expand(
        base,
        parts,
        dironly=dironly,
        recursive=recursive,
        include_hidden=include_hidden,
        case_sensitive=case_sensitive,
        native=native,
        on_error=on_error,
        bound_loops=bound_loops,
        recurse_symlinks=recurse_symlinks,
    )


def _expand(base, parts, *, dironly, **options) -> _ty.Iterator[_Globable]:
    if not parts:
        if base.is_dir() if dironly else base.exists():
            yield base
        return
    yield from select(base, parts, dironly=dironly, **options)


def select(
    base: _Globable,
    parts: _ty.Sequence[str],
    *,
    dironly: bool = False,
    recursive: bool = True,
    include_hidden: bool = True,
    case_sensitive: bool | None = None,
    native: bool = True,
    on_error: "_ty.Callable[[OSError], None] | None" = None,
    bound_loops: bool = False,
    recurse_symlinks: bool = False,
) -> _ty.Iterator[_Globable]:
    """Yield the paths under `base` matching the pattern components `parts`
    (see `parse_pattern()`); the engine behind `Path.glob()`.

    Directory listings go through `_scandir()`; an `OSError` from listing a
    missing or non-directory path selects nothing. "**" (when `recursive`)
    decides recursion from the listing's non-following stat, so it never
    descends into a directory symlink and always terminates. A trailing "**"
    also selects files on Python 3.13+ and directories only before, which is
    the third rule `native=False` pins: it then selects files on every
    version (3.13's rule, the one current pathlib applies).

    With `recurse_symlinks=True` "**" also descends through a symlink to a
    directory, as pathlib 3.13 does, and ends where that would loop: a link
    to a directory the descent is already inside (`st_dev`/`st_ino`) is an
    entry but is not entered, and so is any link beyond the 40th in one
    descent where the stat has no identity.
    """
    # An explicit `case_sensitive` asks for names to be compared, so a literal
    # is looked for in the listing and yields the name as stored, as pathlib
    # does when it is given. "." and ".." are never names to find.
    explicit_case = case_sensitive is not None or (native and _LITERALS_ARE_LISTED)
    if case_sensitive is None:
        case_sensitive = getattr(base, "_is_case_sensitive", True)
    if recursive:
        parts = _collapse_recursive(parts)
    steps: _ty.List[_ty.Tuple[str, _ty.Any]] = []
    for part in parts:
        if recursive and part == RECURSIVE:
            steps.append((part, None))
        elif part in _SPECIAL_PARTS:
            steps.append((part, False))
        elif explicit_case or WILDCARD_PATTERN.search(part):
            steps.append((part, compile_pattern(part, case_sensitive)))
        else:
            steps.append((part, False))
    opts = _Options(
        dironly,
        include_hidden,
        _DOUBLESTAR_SELECTS_FILES if native else True,
        on_error,
        bound_loops,
        _INTERMEDIATE_LITERAL_IS_CHECKED and native,
        _FINAL_LITERAL_IS_NOT_FOLLOWED or not native,
        recurse_symlinks,
        _DIRONLY_LITERAL_IS_LEXISTS and native,
    )
    selected = _select(base, steps, 0, opts, None)
    if sum(1 for _, kind in steps if kind is None) < 2:
        yield from selected
        return
    # Two separate "**" can reach one path along several splits.
    seen = set()
    for path in selected:
        if path not in seen:
            seen.add(path)
            yield path


class _Options(_ty.NamedTuple):
    dironly: bool
    include_hidden: bool
    doublestar_selects_files: bool = _DOUBLESTAR_SELECTS_FILES
    on_error: "_ty.Callable[[OSError], None] | None" = None
    bound_loops: bool = False
    check_intermediate_literal: bool = _INTERMEDIATE_LITERAL_IS_CHECKED
    unfollowed_final_literal: bool = _FINAL_LITERAL_IS_NOT_FOLLOWED
    recurse_symlinks: bool = False
    dironly_literal_is_lexists: bool = False


def _select(
    path: _Globable,
    steps: _ty.Sequence[_ty.Tuple[str, _ty.Any]],
    index: int,
    opts: _Options,
    is_dir: bool | None,
    entries: "_ty.Sequence | None" = None,
) -> _ty.Iterator[_Globable]:
    """Select from `path` with `steps[index:]`. `entries` is `path`'s listing
    when the caller took it already (see `_scan()`)."""
    part, kind = steps[index]
    last = index == len(steps) - 1

    if kind is None:  # "**"
        if is_dir is None and not path.is_dir():
            _report_unlistable(path, opts.on_error)
            return
        if last:
            with_files = opts.doublestar_selects_files and not opts.dironly
            yield from _recurse(
                path,
                opts.include_hidden,
                with_files,
                opts.on_error,
                opts.bound_loops,
                opts.recurse_symlinks,
            )
            return
        for directory, listing in _recurse_dirs(
            path,
            opts.include_hidden,
            opts.on_error,
            opts.bound_loops,
            opts.recurse_symlinks,
        ):
            yield from _select(directory, steps, index + 1, opts, True, listing)
        return

    if kind is False:  # literal component: no listing needed
        if (
            index == 0
            and part == ".."
            and opts.check_intermediate_literal
            and not path.is_dir()
        ):
            # pathlib selects nothing from a base that is not a directory,
            # even where the system would resolve "base/.." by its text.
            _report_unlistable(path, opts.on_error)
            return
        child = _child(path, part, None)
        if not last:
            if (
                opts.check_intermediate_literal
                and part not in _SPECIAL_PARTS
                and not child.is_dir()
            ):
                _report_unlistable(child, opts.on_error)
                return
            yield from _select(child, steps, index + 1, opts, None)
        elif opts.dironly:
            if _names_a_directory(child, opts.dironly_literal_is_lexists):
                yield child
        elif (
            child.exists(follow_symlinks=False)
            if opts.unfollowed_final_literal
            else child.exists()
        ):
            yield child
        return

    need_dir = opts.dironly or not last
    skip_hidden = not opts.include_hidden and not part.startswith(".")
    for child, stat in _scan(path, opts.on_error) if entries is None else entries:
        if skip_hidden and child.is_hidden():
            continue
        if not kind.match(child.name):
            continue
        if need_dir and not _entry_is_dir(child, stat, follow_symlinks=True):
            continue
        if last:
            yield child
        else:
            yield from _select(child, steps, index + 1, opts, True)
