"""Glob patterns: splitting a pattern into components and matching field by field."""

from __future__ import annotations

import fnmatch as _fnmatch
import functools as _func
import re as _re
import sys as _sys
import typing as _ty

from ..exceptions import NonRelativePatternError

RECURSIVE = "**"
ANY_PATTERN = _re.compile(_fnmatch.translate("*"))
WILDCARD_PATTERN = _re.compile("([*?[])")
WILCARD_PATTERN = WILDCARD_PATTERN  # misspelled alias, kept for importers

# Rules the running interpreter changed mid-series: honoured when `native=True`
# (the default), smoothed over when it is False.
#: A trailing "/" selects directories only from 3.11; before, pathlib ignores it.
_TRAILING_SLASH_SELECTS_DIRS = _sys.version_info >= (3, 11)
#: "**" recurses only as the WHOLE component; "a**" raises before 3.13 and is a
#: plain wildcard from 3.13.
_PARTIAL_DOUBLESTAR_ALLOWED = _sys.version_info >= (3, 13)


@_func.lru_cache(maxsize=256, typed=True)
def compile_pattern(pat: str, case_sensitive: bool):
    # re.NOFLAG is 3.11+
    flags = 0 if case_sensitive else _re.IGNORECASE
    return _re.compile(_fnmatch.translate(pat), flags)


def _collapse_recursive(parts: _ty.Iterable[str]) -> _ty.List[str]:
    """Drop repeated consecutive "**" (they select the same paths)."""
    collapsed: _ty.List[str] = []
    for part in parts:
        if part == RECURSIVE and collapsed and collapsed[-1] == RECURSIVE:
            continue
        collapsed.append(part)
    return collapsed


def _fields(segments: _ty.Sequence[str]) -> _ty.List[str]:
    """`segments` as the fields of the path's "/"-joined text: the root is a
    leading empty field (`["", ""]` for the root alone), the empty path has
    none. Empty and "." segments are not names."""
    rooted = bool(segments) and segments[0] == ""
    names = [s for s in (segments[1:] if rooted else segments) if s and s != "."]
    if rooted:
        return ["", *names] if names else ["", ""]
    return names


def full_match(segments: _ty.Sequence[str], pattern: str, case_sensitive: bool) -> bool:
    """Match `segments` against a glob pattern that may contain "**"
    components (pathlib 3.13's PurePath.full_match semantics): a "**" matches
    zero or more segments, except a trailing "**" after other components,
    which needs at least one ("a/**" does not match "a").

    The root is a field of its own, as in the path's text: a rooted pattern
    names it with its leading "/", a lone "*" never matches it (a lone "*"
    matches one or more characters), and a "**" followed by more components
    reaches it only together with a name after it ("**/x" matches "/a/x",
    not "/x"). Empty and "." components are dropped from the pattern. A
    bracket expression never matches a separator (pathlib's regex lets a
    negated one).

    Runs as a set-of-states automaton, O(len(segments) * len(pattern)), so
    repeated "**" cannot backtrack exponentially.
    """
    fields = _fields(segments)
    parts = [part for part in pattern.split("/") if part and part != "."]
    if pattern.startswith("/"):
        parts = ["", *parts] if parts else ["", ""]
    pats = _collapse_recursive(parts)
    end = len(pats)
    last = end - 1

    # A state is (index, stage). Stage 0: about to match pats[index]. Stage 1 is a "**"
    # that has taken one empty field (the root), which does not count yet; stage 2 has
    # taken enough and may take more or hand over to the next component.
    def closure(states: _ty.Set[_ty.Tuple[int, int]]) -> _ty.Set[_ty.Tuple[int, int]]:
        pending = list(states)
        while pending:
            i, stage = pending.pop()
            if stage == 2 or (
                stage == 0
                and i < end
                and pats[i] == RECURSIVE
                # A non-trailing (or sole) "**" may match no field at all.
                and (i < last or end == 1)
            ):
                following = (i + 1, 0)
                if following not in states:
                    states.add(following)
                    pending.append(following)
        return states

    states = closure({(0, 0)})
    for field in fields:
        advanced: _ty.Set[_ty.Tuple[int, int]] = set()
        for i, stage in states:
            if stage:
                advanced.add((i, 2))
            elif i == end:
                continue
            elif pats[i] == RECURSIVE:
                advanced.add((i, 1 if field == "" and i < last else 2))
            elif pats[i] == "*":
                if field:
                    advanced.add((i + 1, 0))
            elif compile_pattern(pats[i], case_sensitive).match(field):
                advanced.add((i + 1, 0))
        if not advanced:
            return False
        states = closure(advanced)
    return (end, 0) in states


def parse_pattern(
    pattern: "str | _ty.Any", *, native: bool = True
) -> _ty.Tuple[_ty.List[str], bool]:
    """Split a relative glob `pattern` (a `/`-separated string or a
    `Pathname`) into its components, and report whether it ended with a
    separator (directories only).

    Raises `ValueError` for an empty pattern and `NonRelativePatternError`
    for an absolute one, as `pathlib.Path.glob()` does. Empty and "."
    components are dropped.

    `native=True` (the default) follows the running interpreter on the two
    rules pathlib changed mid-series: a trailing "/" is ignored before 3.11
    (it selects directories only from 3.11), and a component that merely
    CONTAINS "**" ("a**") raises `ValueError` before 3.13. `native=False`
    applies one rule on every version instead -- a trailing "/" always
    selects directories only, "a**" is always a plain wildcard -- so a
    pattern gives the same answer on every interpreter and every backend.
    """
    if isinstance(pattern, str):
        rooted = pattern.startswith("/")
        segments = pattern.split("/")
    else:
        segments = list(pattern.segments)
        rooted = bool(getattr(pattern, "anchor", "")) or bool(
            getattr(pattern, "source", None)
        )
    if rooted:
        raise NonRelativePatternError("Non-relative patterns are unsupported")
    parts = [part for part in segments if part not in ("", ".")]
    if not parts:
        raise ValueError(f"Unacceptable pattern: {str(pattern)!r}")
    if native and not _PARTIAL_DOUBLESTAR_ALLOWED:
        for part in parts:
            if RECURSIVE in part and part != RECURSIVE:
                # pathlib's own message, so an `except ValueError` that reads
                # it sees the same text it does on this interpreter.
                raise ValueError(
                    "Invalid pattern: '**' can only be an entire path component"
                )
    trailing_sep = bool(segments) and segments[-1] == ""
    if native and not _TRAILING_SLASH_SELECTS_DIRS:
        trailing_sep = False
    return parts, trailing_sep
