"""Glob edge cases, each compared with the `pathlib` of the RUNNING
interpreter over the same tree (`LocalPath`, and a `MemPath` copy where the
rule is not about the local system), plus what `pathlib` does not answer:
listings per directory, `on_error`, audit events and loop bounds."""

import collections
import fnmatch
import itertools
import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

import pathlib_next
from pathlib_next.mempath import MemPath
from pathlib_next import glob as _glob
from pathlib_next import FileStat

WINDOWS = os.name == "nt"

_FILES = (
    "a.txt",
    "b.py",
    "B2.PY",
    ".hidden.txt",
    ".hdir/inner.py",
    "sub/c.py",
    "sub/.dot.py",
    "sub/nested/d.py",
    "sub/nested/deep/e.py",
    "sub2/sub/c.py",
)
_DIRS = ("empty_dir",)


def _build(root: pathlib.Path):
    for rel in _FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(rel)
    for rel in _DIRS:
        (root / rel).mkdir()


def _mem_twin(root: pathlib.Path) -> MemPath:
    twin = MemPath("/tree")
    twin.mkdir()
    for rel in _FILES:
        target = twin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rel)
    for rel in _DIRS:
        (twin / rel).mkdir()
    return twin


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    _build(root)
    return root


def _relative(path, base) -> str:
    text, prefix = path.as_posix(), base.as_posix()
    assert text.startswith(prefix), (text, prefix)
    return text[len(prefix) :].lstrip("/") or "."


def _answer(base, method, pattern, kwargs):
    """What a call selects, as the paths relative to `base`, or what it
    raised (a `NonRelativePatternError` is the `NotImplementedError` that
    pathlib raises)."""
    try:
        return "ok", {
            _relative(p, base) for p in getattr(base, method)(pattern, **kwargs)
        }
    except Exception as error:  # noqa: BLE001
        if isinstance(error, NotImplementedError):
            return "raised", "NotImplementedError"
        return "raised", type(error).__name__


def _compare(tree, impl_base, method, pattern, **kwargs):
    oracle = _answer(tree, method, pattern, kwargs)
    assert _answer(impl_base, method, pattern, kwargs) == oracle, (
        method,
        pattern,
        kwargs,
    )


# --- the anchor of an extended-length path is a prefix, not a wildcard ------


@pytest.mark.skipif(not WINDOWS, reason="extended-length paths are a Windows spelling")
def test_a_carried_pattern_under_an_extended_length_anchor_is_expanded(tree):
    plain = pathlib_next.LocalPath(tree, "*.py")
    extended = pathlib_next.LocalPath("\\\\?\\" + str(tree) + "\\*.py")

    found = sorted(p.name for p in extended.glob(None))

    assert found == sorted(p.name for p in plain.glob(None)) == ["B2.PY", "b.py"]
    assert extended.has_glob_pattern() is plain.has_glob_pattern()


@pytest.mark.skipif(not WINDOWS, reason="extended-length paths are a Windows spelling")
def test_a_carried_extended_length_path_without_a_wildcard_is_found(tree):
    extended = pathlib_next.LocalPath("\\\\?\\" + str(tree / "b.py"))
    assert [p.name for p in extended.glob(None)] == ["b.py"]


# --- an explicit case_sensitive changes the case rule and nothing else ------

_CASE_PATTERNS = [
    ("..", True),
    ("../*", True),
    ("*/..", True),
    ("sub/../*", True),
    ("sub/nested/..", True),
    ("**/..", True),
    ("a.txt", True),
    ("A.TXT", True),
    ("A.TXT", False),
    ("SUB", False),
    ("Sub/*.PY", False),
    (" ", False),
    ("nul", False),
]


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="pathlib takes case_sensitive from 3.12"
)
@pytest.mark.parametrize("pattern, case_sensitive", _CASE_PATTERNS)
def test_explicit_case_sensitive_matches_pathlib(tree, pattern, case_sensitive):
    _compare(
        tree,
        pathlib_next.LocalPath(tree),
        "glob",
        pattern,
        case_sensitive=case_sensitive,
    )
    _compare(
        tree,
        pathlib_next.LocalPath(tree),
        "rglob",
        pattern,
        case_sensitive=case_sensitive,
    )


def test_dot_dot_stays_literal_when_the_case_rule_is_given():
    base = MemPath("/root")
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "f.txt").write_text("x")

    for case_sensitive in (True, False):
        found = {
            p.as_posix()
            for p in base.glob("sub/../sub/*", case_sensitive=case_sensitive)
        }
        assert found == {"/root/sub/../sub/f.txt"}


# --- bound_loops needs a real identity --------------------------------------


class _Status:
    def __init__(self, stat, dev, ino):
        self._stat = stat
        self.st_dev, self.st_ino = dev, ino
        self.st_mode, self.st_size, self.st_mtime = (
            stat.st_mode,
            stat.st_size,
            stat.st_mtime,
        )

    def __getattr__(self, name):
        return getattr(self._stat, name)


def _identified(dev, ino):
    class Identified(MemPath):
        __slots__ = ()

        def stat(self, *, follow_symlinks=True):
            return _Status(super().stat(follow_symlinks=follow_symlinks), dev, ino)

    return Identified


@pytest.mark.parametrize("ino", [0, None])
def test_bound_loops_does_not_drop_directories_on_a_stat_without_an_inode(ino):
    root = _identified(7, ino)("/r")
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "deep.conf").write_text("x")
    (root / "top.conf").write_text("x")

    free = sorted(p.as_posix() for p in root.glob("**/*.conf"))
    bounded = sorted(p.as_posix() for p in root.glob("**/*.conf", bound_loops=True))

    assert bounded == free == ["/r/a/b/deep.conf", "/r/top.conf"]


def test_bound_loops_still_skips_a_directory_that_is_its_own_ancestor():
    class Inodes(MemPath):
        __slots__ = ()

        def stat(self, *, follow_symlinks=True):
            stat = super().stat(follow_symlinks=follow_symlinks)
            # `loop` is another name for its parent: the same identity.
            named = self.parent.as_posix() if self.name == "loop" else self.as_posix()
            return _Status(stat, 7, 1000 + sum(map(ord, named)))

    root = Inodes("/r")
    (root / "a" / "loop").mkdir(parents=True)
    (root / "a" / "loop" / "again.conf").write_text("x")
    (root / "a" / "f.conf").write_text("x")

    free = sorted(p.as_posix() for p in root.glob("**/*.conf"))
    bounded = sorted(p.as_posix() for p in root.glob("**/*.conf", bound_loops=True))

    assert free == ["/r/a/f.conf", "/r/a/loop/again.conf"]
    assert bounded == ["/r/a/f.conf"]


# --- a literal component is checked as the running pathlib checks it --------

_LITERAL_PATTERNS = [
    "a.txt/..",
    "sub/c.py/..",
    "nonexistent/..",
    "nonexistent/*",
    "**/deep/..",
    "**/nonexistent/..",
    "sub/nested/..",
    "sub/nested/../*",
    "sub/../*",
    "../*",
    "a.txt/**",
]


@pytest.mark.parametrize("method", ["glob", "rglob"])
@pytest.mark.parametrize("pattern", _LITERAL_PATTERNS)
def test_localpath_checks_literals_like_the_running_pathlib(tree, method, pattern):
    _compare(tree, pathlib_next.LocalPath(tree), method, pattern)


@pytest.mark.parametrize("method", ["glob", "rglob"])
@pytest.mark.parametrize("pattern", _LITERAL_PATTERNS)
def test_mempath_checks_literals_like_the_running_pathlib(tree, method, pattern):
    if WINDOWS and sys.version_info >= (3, 13) and ".." in pattern:
        pytest.skip(
            "pathlib 3.13+ tests the joined text, which Windows resolves "
            "lexically; MemPath follows the tree"
        )
    _compare(tree, _mem_twin(tree), method, pattern)


def test_a_carried_pattern_checks_its_literals_like_pathlib(tree):
    # `glob(None)` splits at the first wildcard; the literals before it are
    # the base, the ones after it are checked as a pattern's are.
    for tail in ("a.txt/..", "sub/c.py/..", "nonexistent/.."):
        carried = pathlib_next.LocalPath(tree, tail)
        oracle = {
            p.as_posix() for p in pathlib.Path(tree, *tail.split("/")[:-1]).glob("..")
        }
        assert {p.as_posix() for p in carried.glob(None)} == oracle


# --- rglob(".") is every entry below, as pathlib's is -----------------------


@pytest.mark.parametrize("pattern", [".", "./", "./."])
def test_rglob_of_a_pattern_with_no_component_matches_pathlib(tree, pattern):
    _compare(tree, pathlib_next.LocalPath(tree), "rglob", pattern)
    _compare(tree, _mem_twin(tree), "rglob", pattern)


def test_rglob_still_refuses_an_absolute_pattern(tree):
    for base in (pathlib_next.LocalPath(tree), _mem_twin(tree)):
        with pytest.raises(NotImplementedError):
            base.rglob("/x")


def test_glob_of_a_pattern_with_no_component_is_a_value_error(tree):
    with pytest.raises(ValueError):
        pathlib_next.LocalPath(tree).glob(".")
    with pytest.raises(ValueError):
        _mem_twin(tree).glob("")


# --- the last literal is tested as the running pathlib tests it -------------


@pytest.fixture
def links(tree):
    try:
        os.symlink(tree / "gone", tree / "dangling")
        os.symlink(tree / "gone_dir", tree / "dangling_dir", True)
        os.symlink(tree / "a.txt", tree / "link_file")
        os.symlink(tree / "sub", tree / "link_dir", True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks cannot be created here")
    return tree


@pytest.mark.parametrize(
    "pattern",
    [
        "dangling",
        "dangling_dir",
        "link_file",
        "dangling/",
        "dangling_dir/",
        "link_file/",
        "link_dir/",
        "a.txt/",
        "sub/",
        "d*",
    ],
)
def test_a_literal_naming_a_dangling_link_matches_pathlib(links, pattern):
    for method in ("glob", "rglob"):
        _compare(links, pathlib_next.LocalPath(links), method, pattern)


# --- an unlistable base is reported, whatever the first component is --------


def _reported(base, method, pattern):
    seen = []
    list(getattr(base, method)(pattern, on_error=seen.append))
    return [type(error) for error in seen]


@pytest.mark.parametrize("pattern", ["*", "**", "**/*", "x/*", "*/x"])
def test_on_error_hears_of_a_missing_base_for_every_first_component(tmp_path, pattern):
    for base in (
        pathlib_next.LocalPath(tmp_path, "missing"),
        MemPath("/missing"),
    ):
        assert _reported(base, "glob", pattern) == [FileNotFoundError]


@pytest.mark.parametrize("pattern", ["*", "**", "**/*"])
def test_on_error_hears_of_a_file_used_as_a_base(tree, pattern):
    for base in (
        pathlib_next.LocalPath(tree, "a.txt"),
        _mem_twin(tree) / "a.txt",
    ):
        assert _reported(base, "glob", pattern) == [NotADirectoryError]


def test_on_error_is_not_told_about_a_base_that_lists(tree):
    for base in (pathlib_next.LocalPath(tree), _mem_twin(tree)):
        assert _reported(base, "glob", "**") == []
        assert _reported(base, "glob", "**/*.py") == []


# --- a recursive glob lists each directory once -----------------------------


class _Listed(MemPath):
    calls = collections.Counter()

    def _scandir(self):
        type(self).calls[self.as_posix()] += 1
        return super()._scandir()


class _Unreadable(_Listed):
    def _scandir(self):
        type(self).calls[self.as_posix()] += 1
        if self.name == "locked":
            raise PermissionError(13, "denied", str(self))
        return MemPath._scandir(self)


def _listed_tree(cls):
    cls.calls = collections.Counter()
    root = cls("/r")
    root.mkdir()
    for directory in ("a", "a/b", "c", "locked"):
        (root / directory).mkdir()
        (root / directory / "f.txt").write_text("x")
    (root / "top.txt").write_text("x")
    return root


@pytest.mark.parametrize(
    "method, pattern", [("glob", "**/*.txt"), ("glob", "**/*"), ("rglob", "*")]
)
def test_a_recursive_glob_lists_every_directory_once(method, pattern):
    root = _listed_tree(_Listed)
    _Listed.calls.clear()

    found = list(getattr(root, method)(pattern))

    assert len(found) >= 4
    assert set(_Listed.calls.values()) == {1}
    assert set(_Listed.calls) == {"/r", "/r/a", "/r/a/b", "/r/c", "/r/locked"}


@pytest.mark.parametrize("method, pattern", [("glob", "**/*.txt"), ("rglob", "*")])
def test_a_directory_that_cannot_be_listed_is_reported_once(method, pattern):
    root = _listed_tree(_Unreadable)
    seen = []

    list(getattr(root, method)(pattern, on_error=seen.append))

    assert [(type(e), e.filename) for e in seen] == [(PermissionError, "/r/locked")]


def test_the_listing_a_recursive_step_hands_on_is_the_directorys_own():
    root = _listed_tree(_Listed)
    found = sorted(p.as_posix() for p in root.glob("**/*.txt"))
    assert found == [
        "/r/a/b/f.txt",
        "/r/a/f.txt",
        "/r/c/f.txt",
        "/r/locked/f.txt",
        "/r/top.txt",
    ]


# --- two "**" reach a path along several splits; it is yielded once ---------


@pytest.mark.parametrize("pattern", ["**/*/**/*", "**/**/*/*"])
def test_two_recursive_components_yield_a_path_once(tree, pattern):
    found = [p.as_posix() for p in pathlib_next.LocalPath(tree).glob(pattern)]
    assert len(found) == len(set(found))
    assert set(found) == {p.as_posix() for p in tree.glob(pattern)}


# --- a carried pattern is validated when it is carried ----------------------


@pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="a partial ** is a plain wildcard from 3.13"
)
def test_a_carried_partial_recursive_component_raises_at_the_call(tree):
    carried = pathlib_next.LocalPath(tree, "a**")
    with pytest.raises(ValueError):
        carried.glob(None)
    with pytest.raises(ValueError):
        pathlib_next.LocalPath(tree, "sub", "a**").glob(None, recursive=True)
    assert [p.name for p in carried.glob(None, native=False)] == ["a.txt"]


def test_a_carried_pattern_selects_lazily(tree):
    carried = pathlib_next.LocalPath(tree, "*.py")
    selection = carried.glob(None)
    assert iter(selection) is selection
    assert sorted(p.name for p in selection) == sorted(
        p.name for p in tree.glob("*.py")
    )


def test_an_absolute_pattern_is_a_notimplementederror_and_a_valueerror(tree):
    with pytest.raises(_glob.NonRelativePatternError) as info:
        pathlib_next.LocalPath(tree).glob("/abs")
    assert isinstance(info.value, NotImplementedError)
    assert isinstance(info.value, ValueError)


# --- the audit events pathlib raises ----------------------------------------

_AUDIT_SCRIPT = """
import json, os, pathlib, sys
events = []
sys.addaudithook(
    lambda event, args: events.append([event, *map(str, args)])
    if event.startswith("pathlib.Path.") else None
)
root = sys.argv[1]
sys.path.insert(0, sys.argv[2])
from pathlib_next import LocalPath
calls = [
    ("glob", "*.py"), ("rglob", "*.py"), ("glob", "**/*"), ("glob", ""),
    ("rglob", ""), ("glob", "/x"), ("rglob", "sub/*"), ("glob", "sub/"),
]
out = {}
for cls in (pathlib.Path, LocalPath):
    for method, pattern in calls:
        events.clear()
        try:
            list(getattr(cls(root), method)(pattern))
        except Exception:
            pass
        out.setdefault(f"{method}({pattern!r})", []).append(
            [[e[0], os.path.normpath(e[1]), *e[2:]] for e in events]
        )
print(json.dumps(out))
"""


def test_localpath_raises_the_audit_events_pathlib_raises(tree):
    src = str(pathlib.Path(pathlib_next.__file__).resolve().parents[1])
    done = subprocess.run(
        [sys.executable, "-c", _AUDIT_SCRIPT, str(tree), src],
        capture_output=True,
        text=True,
        cwd=str(tree.parent),
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    # The event of the call itself. What stdlib raises after it comes from its
    # own implementation of the walk and differs from version to version.
    for call, (stdlib, ours) in json.loads(done.stdout).items():
        assert stdlib, f"pathlib raised no event for {call}"
        assert ours[:1] == stdlib[:1], call


# --- a literal on a case-insensitive listing (3.12 selects it from the listing)


class _Folding(MemPath):
    """A `MemPath` whose names are compared without regard to case."""

    __slots__ = ()

    @property
    def _is_case_sensitive(self):
        return False


def _folding_tree():
    root = _Folding("/tree")
    root.mkdir()
    (root / "sub" / "nested").mkdir(parents=True)
    (root / "sub" / "nested" / "d.py").write_text("d")
    (root / "a.txt").write_text("a")
    return root


def _found(root, pattern, **kwargs):
    return sorted(_relative(p, root) for p in root.glob(pattern, **kwargs))


@pytest.mark.parametrize(
    "pattern, stored",
    [
        ("SUB", ["sub"]),
        ("A.TXT", ["a.txt"]),
        ("Sub/NESTED", ["sub/nested"]),
        ("SUB/Nested/D.py", ["sub/nested/d.py"]),
        ("sub/NESTED/", ["sub/nested"]),
        ("sub/nested/..", ["sub/nested/.."]),
        ("SUB/../A.TXT", ["sub/../a.txt"]),
        ("missing", []),
    ],
)
def test_a_literal_is_selected_from_the_listing_where_the_version_does_that(
    monkeypatch, pattern, stored
):
    # The rule of CPython 3.12, which no other version has: it is switched on
    # here so every interpreter runs the branch.
    monkeypatch.setattr(_glob, "_LITERALS_ARE_LISTED", True)
    root = _folding_tree()

    assert _found(root, pattern) == stored


def test_a_literal_is_joined_as_typed_where_the_version_does_that(monkeypatch):
    monkeypatch.setattr(_glob, "_LITERALS_ARE_LISTED", False)
    root = _folding_tree()

    assert _found(root, "SUB") == []
    assert _found(root, "sub") == ["sub"]


def test_native_false_joins_a_literal_as_typed_on_every_version(monkeypatch):
    monkeypatch.setattr(_glob, "_LITERALS_ARE_LISTED", True)
    root = _folding_tree()

    assert _found(root, "SUB", native=False) == []
    assert _found(root, "SUB") == ["sub"]


def test_a_literal_listed_by_name_respects_a_case_sensitive_listing(monkeypatch):
    monkeypatch.setattr(_glob, "_LITERALS_ARE_LISTED", True)
    root = _mem_twin(None)

    assert _found(root, "SUB") == []
    assert _found(root, "sub/nested") == ["sub/nested"]


@pytest.mark.parametrize(
    "pattern",
    ["SUB", "Sub/Nested", "A.TXT", "sub/NESTED/D.PY", "SUB/", "b.PY", "B2.py"],
)
def test_a_literal_typed_in_another_case_matches_pathlib(tree, pattern):
    if not (tree / "SUB").exists():
        pytest.skip("the filesystem tells names apart by case")
    for method in ("glob", "rglob"):
        _compare(tree, pathlib_next.LocalPath(tree), method, pattern)


# --- recurse_symlinks=True: "**" descends through symlinks to directories ---


def _symlink(target, link, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks cannot be created here")


@pytest.fixture
def linked(tmp_path):
    """A tree with links to a directory, to a link, to a file and to nothing."""
    top = tmp_path / "linked"
    (top / "real" / "deep").mkdir(parents=True)
    (top / "real" / "f.txt").write_text("f")
    (top / "real" / "deep" / "g.txt").write_text("g")
    (top / "plain").mkdir()
    (top / "plain" / "p.txt").write_text("p")
    _symlink(top / "real", top / "to_real", True)
    _symlink(top / "to_real", top / "chained", True)
    _symlink(top / "real" / "f.txt", top / "alias.txt")
    _symlink(top / "nowhere", top / "dangling")
    return top


def _walked(top, pattern):
    """What `**/<pattern>` selects, by walking with links followed."""
    found = set()
    for directory, names, files in os.walk(top, followlinks=True):
        for name in names + files:
            if fnmatch.fnmatchcase(name, pattern):
                found.add(_relative(pathlib.Path(directory, name), top))
    return found


_LINKED_IMPLS = {
    "LocalPath": lambda top: pathlib_next.LocalPath(top),
    "FileUri": lambda top: pathlib_next.UriPath(top.as_uri()),
}


@pytest.fixture(params=sorted(_LINKED_IMPLS))
def linked_impl(request, linked):
    if request.param == "FileUri":
        pytest.importorskip("uritools")
    return _LINKED_IMPLS[request.param](linked)


@pytest.mark.parametrize("pattern", ["*.txt", "*", "g.txt", "d*"])
def test_recursing_through_symlinks_reaches_what_a_walk_with_links_reaches(
    linked, linked_impl, pattern
):
    found = {
        _relative(p, linked_impl)
        for p in linked_impl.glob(f"**/{pattern}", recurse_symlinks=True)
    }
    assert found == _walked(linked, pattern)
    assert "to_real/deep/g.txt" in _walked(linked, "g.txt")


def test_not_recursing_through_symlinks_stays_out_of_them(linked, linked_impl):
    found = {_relative(p, linked_impl) for p in linked_impl.glob("**/*.txt")}
    assert "to_real/f.txt" not in found
    assert {"real/f.txt", "alias.txt", "plain/p.txt"} <= found
    assert found == {
        _relative(p, linked_impl)
        for p in linked_impl.glob("**/*.txt", recurse_symlinks=False)
    }


@pytest.mark.skipif(
    sys.version_info < (3, 13), reason="pathlib takes recurse_symlinks from 3.13"
)
@pytest.mark.parametrize(
    "method, pattern",
    [
        ("glob", "**"),
        ("glob", "**/"),
        ("glob", "**/*"),
        ("glob", "**/*.txt"),
        ("glob", "**/deep"),
        ("glob", "**/deep/*"),
        ("glob", "to_real/**"),
        ("glob", "*/**/g.txt"),
        ("rglob", "*"),
        ("rglob", "g.txt"),
    ],
)
def test_recurse_symlinks_matches_pathlib_where_there_is_no_loop(
    linked, method, pattern
):
    oracle = {
        _relative(p, linked)
        for p in getattr(linked, method)(pattern, recurse_symlinks=True)
    }
    ours = {
        _relative(p, linked)
        for p in getattr(pathlib_next.LocalPath(linked), method)(
            pattern, recurse_symlinks=True
        )
    }
    assert ours == oracle


@pytest.fixture
def looped(tmp_path):
    top = tmp_path / "looped"
    (top / "a" / "b").mkdir(parents=True)
    (top / "a" / "f.txt").write_text("f")
    (top / "a" / "b" / "h.txt").write_text("h")
    _symlink(top, top / "a" / "to_top", True)
    _symlink(top / "a", top / "a" / "b" / "up", True)
    _symlink(top / "a" / "b", top / "a" / "sibling", True)
    return top


# What a bounded walk may produce at most: a test that fails on a regression
# instead of hanging on it.
_LOOP_BOUND = 5000


@pytest.mark.parametrize("pattern", ["**", "**/*", "**/*.txt", "**/", "a/**/h.txt"])
def test_a_loop_of_links_ends_and_lists_each_entry_once(looped, pattern):
    base = pathlib_next.LocalPath(looped)

    found = list(
        itertools.islice(
            base.glob(pattern, recurse_symlinks=True, native=False), _LOOP_BOUND
        )
    )

    assert 0 < len(found) < _LOOP_BOUND
    assert len(found) == len({p.as_posix() for p in found})


def test_a_loop_of_links_keeps_its_links_as_entries_and_does_not_enter_them(looped):
    base = pathlib_next.LocalPath(looped)

    found = {
        _relative(p, looped)
        for p in itertools.islice(
            base.glob("**", recurse_symlinks=True, native=False), _LOOP_BOUND
        )
    }

    assert found == {
        ".",
        "a",
        "a/f.txt",
        "a/b",
        "a/b/h.txt",
        "a/b/up",
        "a/sibling",
        "a/sibling/h.txt",
        "a/sibling/up",
        "a/to_top",
    }


class _Ring(MemPath):
    """A `MemPath` whose directories each hold a link `loop` to their own
    parent, with a stat that has no identity to tell the loop by."""

    __slots__ = ()

    def _is_loop(self):
        return self.name == "loop"

    def stat(self, *, follow_symlinks=True):
        if self._is_loop():
            if not follow_symlinks:
                return FileStat(stat.S_IFLNK | 0o777)
            return self.parent.stat()
        return super().stat(follow_symlinks=follow_symlinks)

    def _scandir(self):
        if self._is_loop():
            yield from self.parent._scandir()
            return
        yield from super()._scandir()
        yield "loop", FileStat(stat.S_IFLNK | 0o777)


def test_links_with_no_identity_are_followed_at_most_forty_times():
    top = _Ring("/ring")
    top.mkdir()
    (top / "a.txt").write_text("a")

    found = list(
        itertools.islice(top.glob("**/a*.txt", recurse_symlinks=True), _LOOP_BOUND)
    )

    names = [p.as_posix() for p in found]
    assert len(names) == 1 + _glob._MAX_LINK_TRAVERSALS
    assert {"/ring/a.txt", "/ring/loop/a.txt"} <= set(names)
    assert [p.as_posix() for p in top.glob("**/a*.txt")] == ["/ring/a.txt"]


def test_recurse_symlinks_changes_nothing_for_a_backend_without_links():
    root = _mem_twin(None)

    plain = sorted(p.as_posix() for p in root.glob("**/*"))
    followed = sorted(p.as_posix() for p in root.glob("**/*", recurse_symlinks=True))

    assert followed == plain
