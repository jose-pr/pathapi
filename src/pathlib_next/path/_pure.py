"""The pure pathname: `Pathname`, its parents and the name rules it follows."""

from __future__ import annotations

import abc as _abc
import operator as _operator
import os as _os
import re as _re
import sys as _sys
import typing as _ty

from .. import _stubs
from .. import glob as _glob

if _ty.TYPE_CHECKING:
    from ._path import Path


P = _ty.TypeVar("P", bound="Path")
PN = _ty.TypeVar("PN", bound="Pathname")

_P = _ty.TypeVar("_P")
_F = _ty.TypeVar("_F", bound=_ty.Callable[..., _ty.Any])

if _ty.TYPE_CHECKING:

    def _abstract(method: _F) -> _F:
        # `Path(...)` builds a LocalPath, so type checkers must read the bare
        # class as instantiable; at run time ABCMeta still refuses a subclass
        # that leaves one of these out.
        return method

else:
    _abstract = _abc.abstractmethod

# 3.13 refuses an empty stem on a name that has a suffix (`with_stem("")`).
_STEM_KEEPS_SUFFIX = _sys.version_info >= (3, 13)

# A final component's suffix split, following the running interpreter's
# pathlib: 3.14 treats a trailing "." as a suffix ("a." -> ".") and ignores
# leading dots; earlier versions do neither.
if _sys.version_info >= (3, 14):

    def _name_suffix(name: str) -> str:
        name = name.lstrip(".")
        i = name.rfind(".")
        return name[i:] if i != -1 else ""

    def _name_stem(name: str) -> str:
        i = name.rfind(".")
        if i != -1:
            stem = name[:i]
            # The stem must contain at least one non-dot character.
            if stem.lstrip("."):
                return stem
        return name

    def _name_suffixes(name: str) -> "list[str]":
        return ["." + ext for ext in name.lstrip(".").split(".")[1:]]

else:

    def _name_suffix(name: str) -> str:
        i = name.rfind(".")
        return name[i:] if 0 < i < len(name) - 1 else ""

    def _name_stem(name: str) -> str:
        i = name.rfind(".")
        return name[:i] if 0 < i < len(name) - 1 else name

    def _name_suffixes(name: str) -> "list[str]":
        if name.endswith("."):
            return []
        return ["." + suffix for suffix in name.lstrip(".").split(".")[1:]]


class FsPathLike(_ty.Protocol):
    """Anything implementing `__fspath__` -- registered with `os.PathLike`
    so `os.fspath()` and friends accept it."""

    __slots__ = ()

    def __fspath__(self) -> str:
        return _stubs._unimplemented("__fspath__")


_os.PathLike.register(FsPathLike)


class _PathnameParents(_ty.Sequence[PN]):
    """This object provides sequence-like access to the logical ancestors
    of a path.  Don't try to construct it yourself."""

    __slots__ = ("_path", "_anchor", "_segments")

    def __init__(self, path: PN):
        self._path = path
        segments = tuple(path.segments)
        # The anchor (a leading "" for the root) is no ancestor of its own: counting
        # it adds an entry and spells the root as the relative empty path.
        self._anchor = path._anchor_segments()
        segments = segments[len(self._anchor) :]
        while segments and not segments[-1]:
            segments = segments[:-1]
        self._segments = segments

    def __len__(self):
        return len(self._segments)

    @_ty.overload
    def __getitem__(self, idx: slice) -> tuple[PN]: ...
    @_ty.overload
    def __getitem__(self, idx: int) -> PN: ...
    def __getitem__(self, idx: int | slice) -> tuple[PN] | PN:
        if isinstance(idx, slice):
            return tuple(self[i] for i in range(*idx.indices(len(self))))

        if idx >= len(self) or idx < -len(self):
            raise IndexError(idx)
        if idx < 0:
            idx += len(self)
        kept = self._segments[: -idx - 1]
        if not self._anchor:
            return self._path.with_segments(*kept)
        # ("", "") is the root in with_segments' "/"-joined spelling.
        return self._path.with_segments(*self._anchor, *(kept or ("",)))

    def __repr__(self):
        return "<{}.parents>".format(type(self._path).__name__)


class Pathname(FsPathLike, _ty.Generic[_P]):
    """Base class for manipulating paths without I/O."""

    __slots__ = ()

    @property
    def _is_case_sensitive(self) -> bool:
        return True

    @_abstract
    def as_uri(self) -> str:
        """Return the path as a URI string."""
        ...

    @property
    def name(self) -> str:
        """The final path component, if any."""
        segments = self.segments
        return "" if not segments else segments[-1]

    @property
    def suffix(self) -> str:
        """
        The final component's last suffix, if any.

        This includes the leading period. For example: '.txt'
        """
        return _name_suffix(self.name)

    @property
    def suffixes(self):
        """
        A list of the final component's suffixes, if any.

        These include the leading periods. For example: ['.tar', '.gz']
        """
        return _name_suffixes(self.name)

    @property
    def stem(self):
        """The final path component, minus its last suffix."""
        return _name_stem(self.name)

    @property
    @_abstract
    def segments(self) -> _ty.Sequence[str]:
        """The sequence of path component strings."""
        ...

    @property
    @_abstract
    def parts(self) -> _P:
        """The individual components/parts of the path."""
        ...

    @_abstract
    def with_segments(self, *segments: str) -> _ty.Self:
        """Construct a same-type path instance from new segments."""
        ...

    def with_name(self, name: str) -> _ty.Self:
        """Return a new path with the name changed.

        Raises ValueError for an empty name, ".", or a name containing "/",
        as pathlib does. Without the check a name such as "../../etc/passwd"
        or "x/y" was spliced in verbatim, so a caller relying on pathlib's
        validation of an untrusted file name got a path outside the
        directory. A bare ".." is accepted, also as pathlib does; use
        `providers.is_safe_child_name()` to reject it. `with_stem()`/
        `with_suffix()` validate their result through here.
        """
        if not name or "/" in name or name == ".":
            raise ValueError("Invalid name %r" % (name,))
        if not self.name:
            raise ValueError("%r has an empty name" % (self,))
        return self.with_segments(*self.segments[:-1], name)

    def with_stem(self, stem: str) -> _ty.Self:
        """Return a new path with the stem changed (validated like
        `with_name()`). From 3.13 on an empty stem is refused when the name
        has a suffix, as `pathlib` does: it would leave a name made of the
        suffix alone."""
        suffix = self.suffix
        if _STEM_KEEPS_SUFFIX and suffix and not stem:
            raise ValueError("%r has a non-empty suffix" % (self,))
        return self.with_name(stem + suffix)

    def with_suffix(self, suffix: str) -> _ty.Self:
        """Return a new path with the suffix changed or added."""
        name = self.name
        if (
            suffix
            and not suffix.startswith(".")
            or (suffix == "." and _sys.version_info < (3, 14))
        ):
            raise ValueError("Invalid suffix %r" % (suffix))
        if not name:
            raise ValueError("%r has an empty name" % (self,))
        old_suffix = self.suffix
        if not old_suffix:
            name = name + suffix
        else:
            name = name[: -len(old_suffix)] + suffix
        return self.with_name(name)

    @_abstract
    def relative_to(self, other: _ty.Self | str) -> _ty.Self:
        """Return the relative path to another path identified by the passed
        arguments.  If the operation is not possible (because this is not
        related to the other path), raise ValueError.
        """
        ...

    def is_relative_to(self, other: _ty.Self | str):
        """Return True if the path is relative to another path or False."""
        cls = type(self)
        # `other` is parsed standalone, as CPython does, not joined under `self`;
        # `with_segments()` rather than the constructor keeps per-instance state
        # such as MemPath's backend.
        other = other if isinstance(other, cls) else self.with_segments(other)
        return other == self or other in self.parents

    def __eq__(self, other: object) -> bool:
        """Compare by (exact type, segments).

        Identity equality would make `is_relative_to()` (which decides via
        `==`) return False for every subclass and break paths as dict keys
        or set members, so a pure subclass that defines none gets this one.
        `_BaseFSPathname`/`LocalPath` are unaffected: `pathlib.PurePath`
        precedes `Pathname` in their MRO and keeps its own `__eq__`, as
        does any class that defines one.
        """
        if type(self) is not type(other):
            return NotImplemented
        return tuple(self.segments) == tuple(other.segments)

    def __hash__(self) -> int:
        return hash((type(self), tuple(self.segments)))

    def _order_key(self) -> _ty.Any:
        """What `<`, `<=`, `>` and `>=` compare, between two paths of one
        exact type: the segments, as `__eq__` does. A type whose `__eq__`
        compares something else overrides it with that."""
        return tuple(self.segments)

    def _compare(
        self, other: Pathname, compare: _ty.Callable[[_ty.Any, _ty.Any], bool]
    ):
        if type(self) is not type(other):
            return NotImplemented
        return compare(self._order_key(), other._order_key())

    def __lt__(self, other: Pathname) -> bool:
        return self._compare(other, _operator.lt)

    def __le__(self, other: Pathname) -> bool:
        return self._compare(other, _operator.le)

    def __gt__(self, other: Pathname) -> bool:
        return self._compare(other, _operator.gt)

    def __ge__(self, other: Pathname) -> bool:
        return self._compare(other, _operator.ge)

    def __truediv__(self, key: _ty.Self | str) -> _ty.Self:
        try:
            return type(self)(self, key)
        except (TypeError, NotImplementedError):
            return NotImplemented

    def __rtruediv__(self, key: str) -> _ty.Self:
        """`"prefix" / path`, as pathlib supports. `self` is passed as an
        object, so per-instance state it carries (a `MemPath` backend)
        survives, and an absolute `self` restarts the join."""
        if not isinstance(key, str):
            return NotImplemented
        try:
            return type(self)(key, self)
        except (TypeError, NotImplementedError):
            return NotImplemented

    def joinpath(self, *args: str | _ty.Self) -> _ty.Self:
        """Combine this path with one or more paths/segments."""
        return type(self)(self, *args)

    @property
    def root(self) -> str:
        """The root of the path, if any (e.g. "/"). See `docs/divergences.md`
        for how this is derived generically vs. LocalPath's real one."""
        segments = self.segments
        return "/" if segments and not segments[0] else ""

    @property
    def drive(self) -> str:
        """No generic concept of a drive; always "". LocalPath gets a real
        one from pathlib on Windows."""
        return ""

    @property
    def anchor(self) -> str:
        """`drive + root`."""
        return self.drive + self.root

    @property
    @_abstract
    def parent(self) -> _ty.Self:
        """The logical parent of the path."""

    @property
    def parents(self) -> _ty.Sequence[_ty.Self]:
        """An immutable sequence providing access to the logical ancestors of the path."""
        return _PathnameParents(self)

    def _anchor_segments(self) -> tuple[str, ...]:
        """The leading entries of `segments` that anchor the path rather than
        name a directory, for `parents`: `("",)` for a rooted path, else
        `()`. A subclass that spells its root differently overrides it."""
        segments = self.segments
        return ("",) if segments and segments[0] == "" else ()

    def is_absolute(self) -> bool:
        """True if the path is absolute: it has a root. A class whose paths
        can be rooted without being absolute overrides it."""
        return bool(self.root)

    def _match_parts(self) -> tuple[bool, list[str]]:
        """`(anchored, names)`: the path as `match()` sees it -- whether it
        is rooted, and its names with empty and "." segments dropped, the
        way `PurePosixPath` parses a string."""
        segments = self.segments
        anchored = bool(segments) and segments[0] == ""
        return anchored, [s for s in segments if s and s != "."]

    def match(self, path_pattern: str | _re.Pattern, *, case_sensitive=None):
        """
        Return True if this path matches the given glob-style pattern.

        pathlib's semantics on the running interpreter: a relative pattern
        matches the last N segments (from the right), an absolute pattern
        must match the whole path, and each segment is matched on its own,
        so `*` never crosses a "/". "**" acts like "*" (use `full_match()`
        for recursive matching). An empty pattern raises ValueError.

        A compiled `re.Pattern` cannot be split into segments; it is matched
        against the whole path string instead ("/"-joined segments, so a
        URI's scheme and host are never part of it).
        """
        import fnmatch as _fnmatch

        if case_sensitive is None:
            case_sensitive = self._is_case_sensitive
        anchored, names = self._match_parts()
        if isinstance(path_pattern, _re.Pattern):
            path = ("/" if anchored else "") + "/".join(names)
            return path_pattern.match(path) is not None
        if isinstance(path_pattern, Pathname):
            path_pattern = "/".join(path_pattern.segments)
        elif not isinstance(path_pattern, str):
            path_pattern = _os.fspath(path_pattern)
        pattern_anchored = path_pattern.startswith("/")
        pattern_names = [s for s in path_pattern.split("/") if s and s != "."]
        if not pattern_names and not pattern_anchored:
            raise ValueError("empty pattern")
        if _sys.version_info[:2] == (3, 12):
            # 3.12 matches the whole path as one string with its separators swapped for
            # newlines, which differs at the edges: see `_match_lines_312`.
            return _match_lines_312(
                anchored, names, pattern_anchored, pattern_names, case_sensitive
            )
        flags = 0 if case_sensitive else _re.IGNORECASE
        if pattern_anchored and not (anchored and len(names) == len(pattern_names)):
            return False
        # The root counts as one more part a relative pattern can reach.
        path_parts = len(names) + anchored
        if len(pattern_names) > path_parts:
            return False
        for index, pattern in enumerate(reversed(pattern_names)):
            if index == len(names):
                # A relative pattern as long as the path reaches its root. 3.13+ compiles the
                # root like any part (a bracket expression matches "/", "*" and "?" do not);
                # 3.9-3.11 fnmatch the root string, so "*" and "?" match it too.
                if _sys.version_info >= (3, 13):
                    from ..fspath import _translate_segment

                    regex = f"(?s:{_translate_segment(pattern, '[^/]')})\\Z"
                    return _re.match(regex, "/", flags) is not None
                return _re.match(_fnmatch.translate(pattern), "/", flags) is not None
            name = names[len(names) - 1 - index]
            if _re.match(_fnmatch.translate(pattern), name, flags) is None:
                return False
        return True

    def full_match(
        self, pattern: str | _os.PathLike, *, case_sensitive: bool | None = None
    ) -> bool:
        """Return True if this path matches the glob-style `pattern`
        against the whole path (3.13 parity). Unlike match(), this isn't a
        right-anchored partial match, and "**" matches any number of path
        segments (including zero). The root is a component of its own: a
        rooted path needs a rooted pattern, or a leading "**" that reaches
        past it (see `glob.full_match`). Empty and "." components of
        the pattern are ignored, as `pathlib` parses its pattern as a path.
        """
        if case_sensitive is None:
            case_sensitive = self._is_case_sensitive
        if isinstance(pattern, Pathname):
            pattern = "/".join(pattern.segments)
        elif not isinstance(pattern, str):
            pattern = _os.fspath(pattern)
        return _glob.full_match(self.segments, pattern, case_sensitive)

    def as_posix(self) -> str:
        """Return the string representation of the path with forward slashes."""
        return "/".join(self.segments)

    def has_glob_pattern(self):
        """Return True if any of the path segments contain glob wildcards.

        The anchor is not one of them: a Windows extended-length path
        (`\\\\?\\C:\\data`, and the `\\\\?\\UNC\\server\\share` form) carries a
        literal `?` in its drive, which is a prefix, not a wildcard -- so a
        caller asking "is this a pattern or a plain path?" got `True` for
        every such path.
        """
        segments = list(self.segments)
        anchor = getattr(self, "anchor", "")
        if anchor and segments and segments[0] == anchor:
            segments = segments[1:]
        for segment in segments:
            if _glob.WILDCARD_PATTERN.search(segment) is not None:
                return True
        return False


PurePathLike = _ty.Union[str, Pathname]

#: `fnmatch.translate()` wraps its output in "(?s:...)\\Z"; 3.12 strips that
#: to splice parts together, which is what drops the DOTALL flag and lets a
#: bracket expression -- but not "." -- match the newline separator.
_FNMATCH_SLICE = slice(len("(?s:"), -len(")\\Z"))


#: 3.12 swaps in both directions, so a newline inside a name becomes a "/" and
#: can never act as a separator.
_SWAP_SEP_AND_NEWLINE = str.maketrans({"/": "\n", "\n": "/"})


def _match_lines_312(
    anchored: bool,
    names: "_ty.Sequence[str]",
    pattern_anchored: bool,
    pattern_names: "_ty.Sequence[str]",
    case_sensitive: bool,
) -> bool:
    """`match()` as Python 3.12 implements it, for 3.12 only.

    3.12 swaps separators for newlines and matches the resulting strings
    with one regex (`_compile_pattern_lines` in its `pathlib`), instead of
    comparing parts pairwise the way every other version does. The
    difference shows at the edges: the root is "\n", a single line that a
    part matching the empty string ("**", but not "*", which 3.12 compiles
    as ".+") reaches from either side, and a bracket expression such as
    "[!a]" can consume the newline itself. Porting the algorithm keeps those
    edges right without enumerating them.
    """
    import fnmatch as _fnmatch

    # pathlib spells an empty path "." and gives it no lines at all.
    path_lines = (("/" if anchored else "") + "/".join(names)).translate(
        _SWAP_SEP_AND_NEWLINE
    )
    pattern_lines = (
        ("/" if pattern_anchored else "") + "/".join(pattern_names)
    ).translate(_SWAP_SEP_AND_NEWLINE)
    parts = ["^"]
    for part in pattern_lines.splitlines(keepends=True):
        if part == "*\n":
            part = r".+\n"
        elif part == "*":
            part = r".+"
        else:
            part = _fnmatch.translate(part)[_FNMATCH_SLICE]
        parts.append(part)
    parts.append(r"\Z")
    flags = _re.MULTILINE
    if not case_sensitive:
        flags |= _re.IGNORECASE
    regex = _re.compile("".join(parts), flags=flags)
    # An anchored pattern must match the whole path, a relative one its tail.
    if pattern_anchored:
        return regex.match(path_lines) is not None
    return regex.search(path_lines) is not None
