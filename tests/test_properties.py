"""Property-based tests (hypothesis) for the generic `Pathname` code: parity
with `pathlib.PurePosixPath` on the operations that are supposed to behave
identically (see `docs/divergences.md` for the ones that deliberately
don't -- those are out of scope here, not asserted against).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from pathapi.mempath import MemPath, MemPathBackend  # noqa: E402

# A "safe" path segment: letters/digits/-/_ only, never empty, never "."
# or ".." -- keeps every property below unambiguous without wading into
# the documented "we never resolve .." / dot-collapsing divergences.
segment = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=1,
    max_size=12,
).filter(lambda s: s not in (".", ".."))

segments_list = st.lists(segment, min_size=0, max_size=6)
nonempty_segments_list = st.lists(segment, min_size=1, max_size=6)


def _posix(segs: list[str]) -> str:
    """Absolute posix path string for a list of segments."""
    return "/" + "/".join(segs)


# --- Path joining ---


@given(base=segments_list, a=segment, b=segment)
@settings(max_examples=200)
def test_join_is_associative(base, a, b):
    u = MemPath(_posix(base))
    assert (u / a) / b == u.joinpath(a, b)


@given(base=segments_list, tail=nonempty_segments_list)
@settings(max_examples=200)
def test_joinpath_matches_repeated_truediv(base, tail):
    u = MemPath(_posix(base))
    joined = u.joinpath(*tail)
    stepped = u
    for part in tail:
        stepped = stepped / part
    assert joined == stepped


# --- Parity vs pathlib.PurePosixPath ---


@given(segs=nonempty_segments_list)
@settings(max_examples=200)
def test_segments_match_pure_posix_path_parts(segs):
    path_str = _posix(segs)
    u = MemPath(path_str)
    pp = pathlib.PurePosixPath(path_str)
    # MemPath.segments keeps a leading "" to mark the root ("/a/b" -> ("", "a",
    # "b")); PurePosixPath.parts marks it as "/" instead ("/a/b" -> ("/",
    # "a", "b")). Everything after that first element matches exactly.
    assert u.segments[0] == ""
    assert u.segments[1:] == pp.parts[1:]


@given(segs=nonempty_segments_list)
@settings(max_examples=200)
def test_name_matches_pure_posix_path(segs):
    path_str = _posix(segs)
    assert MemPath(path_str).name == pathlib.PurePosixPath(path_str).name


@given(common=segments_list, tail=nonempty_segments_list)
@settings(max_examples=200)
def test_relative_to_matches_pure_posix_path(common, tail):
    child_segs = common + tail
    child_str = _posix(child_segs)
    base_str = _posix(common)

    backend = MemPathBackend()
    u_child = MemPath(child_str, backend=backend)
    u_base = MemPath(base_str, backend=backend)
    pp_child, pp_base = pathlib.PurePosixPath(child_str), pathlib.PurePosixPath(
        base_str
    )

    u_rel = u_child.relative_to(u_base)
    pp_rel = pp_child.relative_to(pp_base)
    assert u_rel.segments == pp_rel.parts


@given(a=nonempty_segments_list, b=nonempty_segments_list)
@settings(max_examples=200)
def test_relative_to_unrelated_raises_on_both(a, b):
    # Force genuinely unrelated paths (b must not start with a, and vice
    # versa) by prefixing each with a distinct marker segment.
    a_str = _posix(["left-marker"] + a)
    b_str = _posix(["right-marker"] + b)

    u_raises = False
    try:
        backend = MemPathBackend()
        MemPath(a_str, backend=backend).relative_to(MemPath(b_str, backend=backend))
    except ValueError:
        u_raises = True

    pp_raises = False
    try:
        pathlib.PurePosixPath(a_str).relative_to(pathlib.PurePosixPath(b_str))
    except ValueError:
        pp_raises = True

    assert u_raises == pp_raises == True


@given(segs=nonempty_segments_list)
@settings(max_examples=200)
def test_match_trailing_segment_wildcard_matches_pure_posix_path(segs):
    path_str = _posix(segs)
    pattern = "*" + segs[-1][1:] if len(segs[-1]) > 1 else "*"
    assert MemPath(path_str).match(pattern) == pathlib.PurePosixPath(path_str).match(
        pattern
    )


@given(segs=nonempty_segments_list)
@settings(max_examples=200)
def test_is_relative_to_matches_pure_posix_path(segs):
    path_str = _posix(segs)
    parent_str = _posix(segs[:-1])
    u, pp = MemPath(path_str), pathlib.PurePosixPath(path_str)
    u_parent, pp_parent = MemPath(parent_str), pathlib.PurePosixPath(parent_str)
    assert u.is_relative_to(u_parent) == pp.is_relative_to(pp_parent) == True
    # The str form must agree with the object form: it used to anchor
    # `other` at self's root (MemPath(self, _ROOT, other)) and answer False for
    # every *relative* self.
    assert u.is_relative_to(parent_str) == pp.is_relative_to(parent_str) == True


@given(segs=nonempty_segments_list)
@settings(max_examples=200)
def test_is_relative_to_str_matches_pure_posix_path_for_unrelated_other(segs):
    # The negative direction of the same oracle: a str `other` that is not a
    # prefix must answer False for both, relative and absolute self alike.
    other_str = _posix(["zzz-unrelated"])
    for path_str in (_posix(segs), "/" + _posix(segs)):
        u, pp = MemPath(path_str), pathlib.PurePosixPath(path_str)
        assert u.is_relative_to(other_str) == pp.is_relative_to(other_str)


# --- Generic Pathname (MemPath) vs PurePosixPath: match, parents,
# MemPath normalization. Small alphabets so wildcards actually hit. ---

_match_name = st.sampled_from(["a", "b", "ab", "a.py", ".a", "b.a"])
_match_pattern_part = st.sampled_from(
    ["a", "b", "*", "?", "a*", "*.py", "[ab]", "[!a]*", "**", ".*", "."]
)


def _generic_classes():
    return (MemPath,)


@given(
    rooted=st.booleans(),
    names=st.lists(_match_name, min_size=0, max_size=4),
    pattern_rooted=st.booleans(),
    pattern_parts=st.lists(_match_pattern_part, min_size=0, max_size=4),
    case_sensitive=st.sampled_from([None, True, False]),
)
@settings(max_examples=400)
def test_generic_match_matches_pure_posix_path(
    rooted, names, pattern_rooted, pattern_parts, case_sensitive
):
    """Multi-segment and rooted patterns (not just a trailing "*"): right
    anchoring, `*` never crossing "/", anchors, and the empty pattern."""
    path_str = ("/" if rooted else "") + "/".join(names)
    pattern = ("/" if pattern_rooted else "") + "/".join(pattern_parts)
    kwargs = {} if case_sensitive is None else {"case_sensitive": case_sensitive}

    def outcome(p):
        try:
            if isinstance(p, pathlib.PurePath) and sys.version_info < (3, 12):
                # 3.9-3.11 stdlib has no case_sensitive=; posix is sensitive.
                if case_sensitive is False:
                    return pathlib.PurePosixPath(str(p).lower()).match(pattern.lower())
                return p.match(pattern)
            return p.match(pattern, **kwargs)
        except ValueError:
            return ValueError

    expected = outcome(pathlib.PurePosixPath(path_str))
    for cls in _generic_classes():
        assert outcome(cls(path_str)) == expected, (cls, path_str, pattern)


@given(rooted=st.booleans(), segs=segments_list)
@settings(max_examples=200)
def test_generic_parents_and_parent_match_pure_posix_path(rooted, segs):
    path_str = ("/" if rooted else "") + "/".join(segs)
    pp = pathlib.PurePosixPath(path_str)

    def spell(p):
        s = p.as_posix()
        return "" if s == "." else s

    for cls in _generic_classes():
        p = cls(path_str)
        assert [spell(x) for x in p.parents] == [spell(x) for x in pp.parents]
        assert spell(p.parent) == spell(pp.parent)
        for ancestor in pp.parents:
            assert p.is_relative_to(spell(ancestor)) is True


_mem_piece = st.sampled_from(["", ".", "a", "b", "cd"])


@given(
    args=st.lists(
        st.tuples(st.booleans(), st.lists(_mem_piece, max_size=4)),
        min_size=1,
        max_size=3,
    )
)
@settings(max_examples=300)
def test_mempath_normalizes_like_pure_posix_path(args):
    # POSIX keeps exactly two leading slashes as a distinct root; MemPath has
    # one root, so such arguments are not generated.
    strs = [("/" if rooted else "") + "/".join(pieces) for rooted, pieces in args]
    strs = [s for s in strs if not (s.startswith("//") and not s.startswith("///"))]
    if not strs:
        return
    pp = pathlib.PurePosixPath(*strs)
    ours = MemPath(*strs)
    assert str(ours) == ("" if str(pp) == "." else str(pp))
    assert ours == MemPath(str(pp)) or str(pp) == "."
    assert hash(ours) == hash(MemPath(ours.as_posix()))
    assert ours.name == pp.name
    assert ours.root == pp.root


# --- Names that a URI syntax would give a meaning to --------------------------
#
# The properties above draw segments from letters, digits, "-" and "_". These
# draw any text a POSIX file name may hold, and a set biased towards "?", "#",
# "%", ":", space, "\", "[" and non-ASCII letters.

BACKSLASH = chr(92)
_any_name = st.text(
    alphabet=st.characters(blacklist_characters="/\x00", blacklist_categories=("Cs",)),
    min_size=1,
    max_size=10,
).filter(lambda s: s not in (".", ".."))
_spicy_name = st.text(
    alphabet="ab?#%20: @[]" + BACKSLASH + "+&=;~é.", min_size=1, max_size=8
).filter(lambda s: s not in (".", ".."))
_name = st.one_of(_any_name, _spicy_name)


@given(name=_name)
@settings(max_examples=300)
def test_a_mempath_child_keeps_the_name_it_was_given(name):
    assert (MemPath("/d") / name).name == name


@given(
    name=st.one_of(
        _any_name,
        st.text(alphabet="ab.: /~$%CONnul1*?<>|'" + BACKSLASH, min_size=1, max_size=8),
    )
)
@settings(max_examples=300)
def test_a_name_called_safe_is_one_component_on_both_path_flavours(name):
    from pathapi.providers import is_safe_child_name

    if is_safe_child_name(name):
        joined = pathlib.PurePosixPath("/d") / name
        assert joined.parent == pathlib.PurePosixPath("/d")
        assert joined.name == name
    if is_safe_child_name(name, windows=True):
        joined = pathlib.PureWindowsPath("C:/d") / name
        assert joined.parent == pathlib.PureWindowsPath("C:/d"), (name, joined)
        assert joined.drive == "C:"


@given(name=st.text(alphabet="ab./: " + BACKSLASH + chr(0), max_size=14))
@settings(max_examples=300)
def test_an_archive_member_name_normalizes_once_and_stays_inside(name):
    from pathapi.archive._names import _normalize_member_name

    normal = _normalize_member_name(name)
    if normal is None:
        return
    parts = normal.rstrip("/").split("/")
    assert not normal.startswith("/")
    assert ".." not in parts
    assert "" not in parts or normal == ""
    assert chr(0) not in normal
    assert _normalize_member_name(normal) == normal
