"""What an operation that lists an archive meets when it follows links: a
listing goes through at most one directory link per path and never enters a
link to the directory that holds it or to one of that directory's ancestors, so
the entries any recursive operation visits are bounded by
`(members + links) * (directory links + 1)` however the links point. The
archive is untrusted input: a few hundred bytes of links to `.` used to make
`walk(follow_symlinks=True)`, `glob(recurse_symlinks=True)`, `copy()` and
`PathSyncer` visit one path per route, up to the link bound each.

Each test counts the entries every listing yields and stops at once past
`RUNAWAY`, so a regression fails instead of running without end."""

import errno
import os
import posixpath

import pytest

pytest.importorskip("uritools")

from test_archive_links import FORMATS, _archive, _root

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.archive._base import ArchiveUri
from pathlib_next.sync import PathSyncer

RUNAWAY = 100_000


def _file(name, data=b"x"):
    return (name, "file", data)


def _dir(name):
    return (name + "/", "dir", None)


def _link(name, text):
    return (name, "link", text)


#: Five links to the directory that holds them, and three files.
LOOPS = [
    _file("f1", b"1"),
    _file("f2", b"2"),
    _file("f3", b"3"),
    *(_link(name, ".") for name in "abcde"),
]

#: Twelve directories, each holding two links to the next one.
NESTED = [
    *(_dir("/".join(f"d{n}" for n in range(depth + 1))) for depth in range(12)),
    *(
        _link("/".join([*(f"d{n}" for n in range(depth + 1)), side]), f"d{depth + 1}")
        for depth in range(11)
        for side in ("l", "r")
    ),
    _file("/".join(f"d{n}" for n in range(12)) + "/f", b"deep"),
]

#: A link to a sibling directory, and links to it from two places.
ROUTES = [
    _dir("d"),
    _file("d/x.txt", b"x"),
    _link("d/m", "../e"),
    _dir("e"),
    _file("e/y.txt", b"y"),
    _link("l1", "d"),
    _link("l2", "d"),
]

#: What a tree of links to siblings, as a distribution archive has.
LIBS = [
    _dir("lib"),
    _file("lib/a.txt", b"A"),
    _file("lib/b.txt", b"B"),
    _link("lib64", "lib"),
    _dir("data"),
    _file("data/x.txt", b"X"),
    _link("data/to_a", "../lib/a.txt"),
    _link("viafile", "lib64/a.txt"),
]

#: A file reached through two directory links.
DEEP = [
    _dir("real"),
    _file("real/f.txt", b"deep"),
    _dir("mid"),
    _link("mid/l2", "../real"),
    _link("l1", "mid"),
]


def _bound(tree):
    """`(members + links) * (directory links + 1)` for `tree`."""
    directories = {""}
    for name, kind, _text in tree:
        if kind == "dir":
            directories.add(name.rstrip("/"))
    directory_links = 0
    for name, kind, text in tree:
        if kind == "link":
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), text))
            directory_links += (target if target != "." else "") in directories
    return len(tree) * (directory_links + 1)


class Entries:
    def __init__(self):
        self.count = 0

    def reset(self):
        self.count = 0


@pytest.fixture
def entries(monkeypatch):
    seen = Entries()
    original = ArchiveUri._scandir

    def counted(self):
        for item in original(self):
            seen.count += 1
            if seen.count > RUNAWAY:
                pytest.fail(f"a listing went past {RUNAWAY} entries")
            yield item

    monkeypatch.setattr(ArchiveUri, "_scandir", counted)
    return seen


@pytest.fixture(params=FORMATS)
def fmt(request):
    return request.param


def _open(tmp_path, fmt, tree, name):
    return _root(_archive(tmp_path, fmt, tree, name), fmt)


def _files(top):
    """The files below a `MemPath` or a `LocalPath`, relative to it."""
    base = top.as_posix().rstrip("/")
    return sorted(
        (path / name).as_posix()[len(base) + 1 :]
        for path, _dirs, names in top.walk()
        for name in names
    )


def _swallow(errors):
    def handle(error, *_rest):
        errors.append(error)
        return True

    return handle


def _loops(error):
    return getattr(error, "errno", None) == errno.ELOOP


# --- five links to the directory that holds them ---------------------------------------


def test_a_following_walk_lists_a_link_to_its_own_directory_and_does_not_enter_it(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, LOOPS, "loops")
    walked = [
        (str(top.path), sorted(d), sorted(f))
        for top, d, f in root.walk(follow_symlinks=True)
    ]
    assert walked == [("", [], ["a", "b", "c", "d", "e", "f1", "f2", "f3"])]
    assert entries.count == 8 <= _bound(LOOPS)


def test_a_recursive_glob_through_links_lists_each_entry_once(tmp_path, fmt, entries):
    root = _open(tmp_path, fmt, LOOPS, "loops")
    found = sorted(str(p.path) for p in root.glob("**/*", recurse_symlinks=True))
    assert found == ["a", "b", "c", "d", "e", "f1", "f2", "f3"]
    assert entries.count <= _bound(LOOPS)


def test_a_following_copy_holds_each_file_once_and_reports_each_link(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, LOOPS, "loops")
    errors = []
    memory = MemPath("/out")
    root.copy(memory, recursive=True, ignore_error=errors.append)
    assert _files(memory) == ["f1", "f2", "f3"]
    assert [bool(_loops(e)) for e in errors] == [True] * 5
    assert entries.count <= _bound(LOOPS)
    entries.reset()
    local = tmp_path / "out"
    errors.clear()
    root.copy(LocalPath(local), recursive=True, ignore_error=errors.append)
    assert sorted(os.listdir(local)) == ["f1", "f2", "f3"]
    assert len(errors) == 5 and all(_loops(e) for e in errors)
    assert entries.count <= _bound(LOOPS)


def test_a_following_copy_without_a_handler_stops_at_the_first_link(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, LOOPS, "loops")
    with pytest.raises(OSError) as raised:
        root.copy(MemPath("/out"), recursive=True)
    assert raised.value.errno == errno.ELOOP
    assert entries.count <= _bound(LOOPS)


def test_a_following_sync_holds_each_file_once_and_reports_each_link(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, LOOPS, "loops")
    errors = []
    memory = MemPath("/sync")
    PathSyncer(ignore_error=_swallow(errors)).sync(root, memory)
    assert _files(memory) == ["f1", "f2", "f3"]
    assert len(errors) == 5 and all(_loops(e) for e in errors)
    assert entries.count <= _bound(LOOPS)


def test_a_removal_that_follows_links_removes_the_links_and_nothing_behind_them(
    tmp_path, entries
):
    root = _open(tmp_path, "zip", LOOPS, "loops")
    root.rm(recursive=True, follow_symlinks=True)
    assert list(root.iterdir()) == []
    assert entries.count <= _bound(LOOPS)


# --- twelve directories, each with two links to the next ----------------------------------


def test_nested_links_are_bounded_for_every_operation(tmp_path, fmt, entries):
    root = _open(tmp_path, fmt, NESTED, "nested")
    bound = _bound(NESTED)
    assert bound == 35 * 23

    tops = [str(top.path) for top, _d, _f in root.walk(follow_symlinks=True)]
    assert entries.count <= bound
    assert "d0/d1/l/d3" in tops and "d0/l/l" not in tops
    entries.reset()

    found = list(root.glob("**/*", recurse_symlinks=True))
    assert len(found) <= bound
    assert entries.count <= bound
    entries.reset()

    errors = []
    memory = MemPath("/out")
    root.copy(memory, recursive=True, ignore_error=errors.append)
    assert entries.count <= bound
    # the file is copied once by its own path and once per link that leads to
    # the directory above it: two links at each of the eleven levels
    assert sum(1 for name in _files(memory) if name.endswith("/f")) == 1 + 22
    assert all(_loops(e) for e in errors)
    entries.reset()

    errors.clear()
    PathSyncer(ignore_error=_swallow(errors)).sync(root, MemPath("/sync"))
    assert entries.count <= bound


# --- one link per path ---------------------------------------------------------------------


def test_a_listing_enters_a_link_to_a_sibling_once_and_not_a_link_met_beyond_it(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, ROUTES, "routes")
    tops = sorted(str(top.path) for top, _d, _f in root.walk(follow_symlinks=True))
    assert tops == ["", "d", "d/m", "e", "l1", "l2"]
    assert entries.count <= _bound(ROUTES)
    entries.reset()

    errors = []
    memory = MemPath("/out")
    root.copy(memory, recursive=True, ignore_error=errors.append)
    # x.txt by its own directory and through each of the two links to it, y.txt
    # by its own directory and through the link `d/m`; `l1/m` and `l2/m` are
    # links met after a link and are not entered
    assert _files(memory) == [
        "d/m/y.txt",
        "d/x.txt",
        "e/y.txt",
        "l1/x.txt",
        "l2/x.txt",
    ]
    assert len(errors) == 2 and all(_loops(e) for e in errors)
    assert entries.count <= _bound(ROUTES)


def test_the_link_met_after_a_link_is_a_link_that_is_not_entered(tmp_path, fmt):
    root = _open(tmp_path, fmt, ROUTES, "routes")
    beyond = root / "l1" / "m"
    assert beyond.is_symlink()
    assert not beyond.is_dir()
    assert not beyond.exists()
    with pytest.raises(OSError) as raised:
        beyond.stat()
    assert raised.value.errno == errno.ELOOP
    assert beyond.stat(follow_symlinks=False).st_size == len("../e")
    # the first link is entered
    assert (root / "d" / "m").is_dir()
    assert (root / "l1").is_dir()


def test_a_link_to_its_own_ancestor_is_not_entered_by_a_following_stat(tmp_path, fmt):
    root = _open(tmp_path, fmt, [*ROUTES, _link("d/up", "..")], "up")
    up = root / "d" / "up"
    assert up.is_symlink()
    assert not up.is_dir()
    with pytest.raises(OSError) as raised:
        up.stat()
    assert raised.value.errno == errno.ELOOP
    tops = sorted(str(top.path) for top, _d, _f in root.walk(follow_symlinks=True))
    assert "d/up" not in tops


# --- what still works ---------------------------------------------------------------------


def test_a_link_to_a_sibling_directory_is_entered_and_its_files_are_copied(
    tmp_path, fmt, entries
):
    root = _open(tmp_path, fmt, LIBS, "libs")
    walked = {
        str(top.path): (sorted(d), sorted(f))
        for top, d, f in root.walk(follow_symlinks=True)
    }
    assert walked["lib64"] == ([], ["a.txt", "b.txt"])
    assert list(walked).count("lib64") == 1
    entries.reset()
    memory = MemPath("/out")
    root.copy(memory, recursive=True)
    assert _files(memory) == [
        "data/to_a",
        "data/x.txt",
        "lib/a.txt",
        "lib/b.txt",
        "lib64/a.txt",
        "lib64/b.txt",
        "viafile",
    ]
    assert (memory / "lib64" / "b.txt").read_bytes() == b"B"
    assert (memory / "data" / "to_a").read_bytes() == b"A"
    assert (memory / "viafile").read_bytes() == b"A"
    assert entries.count <= _bound(LIBS)


def test_a_link_to_a_file_inside_a_linked_directory_is_read(tmp_path, fmt):
    root = _open(tmp_path, fmt, LIBS, "libs")
    assert (root / "viafile").read_bytes() == b"A"
    assert (root / "viafile").is_file()
    assert (root / "data" / "to_a").read_bytes() == b"A"


def test_a_path_spelled_through_two_directory_links_resolves(tmp_path, fmt):
    root = _open(tmp_path, fmt, DEEP, "deep")
    path = root / "l1" / "l2" / "f.txt"
    assert path.read_bytes() == b"deep"
    assert path.exists() and path.is_file()
    assert path.stat().st_size == 4
    # a listing the caller spells goes as far as the path says
    assert [p.name for p in (root / "l1" / "l2").iterdir()] == ["f.txt"]
    walked = [
        str(top.path) for top, _d, _f in (root / "l1" / "l2").walk(follow_symlinks=True)
    ]
    assert walked == ["l1/l2"]
    # but a walk from above stops at the second link
    tops = sorted(str(top.path) for top, _d, _f in root.walk(follow_symlinks=True))
    assert tops == ["", "l1", "mid", "mid/l2", "real"]
    assert not (root / "l1" / "l2").is_dir()


# --- without following, nothing changes ----------------------------------------------------


def test_without_following_each_link_is_listed_once_and_never_entered(
    tmp_path, fmt, entries
):
    for name, tree in (("loops", LOOPS), ("nested", NESTED), ("routes", ROUTES)):
        root = _open(tmp_path, fmt, tree, name)
        entries.reset()
        listed = [name for _top, d, f in root.walk() for name in (*d, *f)]
        assert len(listed) == len(tree)
        assert entries.count == len(tree)
        entries.reset()
        assert len(list(root.glob("**/*"))) == len(tree)
        assert entries.count == len(tree)


def test_copying_the_links_without_following_makes_the_links(tmp_path, fmt, entries):
    probe = tmp_path / "probe"
    try:
        os.symlink("x", probe)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create a symbolic link")
    probe.unlink()
    root = _open(tmp_path, fmt, LOOPS, "loops")
    out = tmp_path / "out"
    root.copy(LocalPath(out), recursive=True, follow_symlinks=False)
    assert sorted(os.listdir(out)) == sorted(name for name, _k, _t in LOOPS)
    assert all((out / name).is_symlink() for name in "abcde")
    assert entries.count == 8
