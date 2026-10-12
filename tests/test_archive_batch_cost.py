"""What a zip batch costs as its changes pile up: the member list (and the
directory tree with it) is derived a constant number of times however many
members the block renames, deletes or overwrites, and each change patches it,
so the result equals the one the same changes give one at a time.

The constant is `DERIVATIONS`: one derivation for the archive as the block finds
it (the lookup that decides the first change) and one for the batch once it
holds a change; nothing later derives it again unless a name is spelled two ways
in the archive, where a change cannot be patched."""

import random
import zipfile

import pytest

pytest.importorskip("uritools")

from test_archive_batch import _fields, _members, _write_zip

from pathlib_next import LocalPath
from pathlib_next.uri import UriPath
from pathlib_next.archive import _snapshot as _base
from pathlib_next.archive._zip import _ZipBackend
from pathlib_next.archive._zipentries import _NO_LINK
from pathlib_next.sync import PathSyncer

DERIVATIONS = 2
MEMBERS = 1000


def _zip_uri(path, inner=""):
    return f"zip:{path.as_uri()}!/{inner}"


@pytest.fixture
def derived(monkeypatch):
    """One entry per derivation of a member list."""
    calls = []
    original = _base._Snapshot.__init__

    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_base._Snapshot, "__init__", counted)
    return calls


def _many(path, count=MEMBERS):
    return _write_zip(
        path,
        [("seed.txt", b"seed")]
        + [(f"d{i % 10}/f{i}.txt", b"x" * 50) for i in range(count)],
    )


def _shape(path):
    """Everything of an archive but the times of the members written now."""
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return archive.comment, [
            (
                info.filename,
                archive.read(info),
                info.compress_type,
                info.external_attr,
            )
            for info in archive.infolist()
        ]


# --- a constant number of derivations ----------------------------------------------------


def test_a_thousand_renames_derive_the_member_list_a_constant_number_of_times(
    tmp_path, derived
):
    archive = _many(tmp_path / "a.zip")
    root = UriPath(_zip_uri(archive))
    with root.batch():
        for i in range(MEMBERS):
            (root / f"d{i % 10}" / f"f{i}.txt").rename(f"g{i}.txt")
    assert len(derived) <= DERIVATIONS
    expected = tmp_path / "b.zip"
    _many(expected)
    UriPath(_zip_uri(expected)).backend._rewrite(
        rename={f"d{i % 10}/f{i}.txt": f"d{i % 10}/g{i}.txt" for i in range(MEMBERS)}
    )
    assert _shape(archive) == _shape(expected)


def test_a_thousand_deletes_derive_the_member_list_a_constant_number_of_times(
    tmp_path, derived
):
    archive = _many(tmp_path / "a.zip")
    root = UriPath(_zip_uri(archive))
    with root.batch():
        for i in range(MEMBERS):
            (root / f"d{i % 10}" / f"f{i}.txt").unlink()
    assert len(derived) <= DERIVATIONS
    expected = tmp_path / "b.zip"
    _many(expected)
    UriPath(_zip_uri(expected)).backend._rewrite(
        exclude={f"d{i % 10}/f{i}.txt" for i in range(MEMBERS)},
        # every directory is left with nothing: each keeps its own entry
        overwrite={f"d{n}/": b"" for n in range(10)},
    )
    assert _shape(archive) == _shape(expected)
    assert sorted(p.name for p in root.iterdir()) == [f"d{n}" for n in range(10)] + [
        "seed.txt"
    ]


def test_a_thousand_overwrites_derive_the_member_list_a_constant_number_of_times(
    tmp_path, derived
):
    archive = _many(tmp_path / "a.zip")
    root = UriPath(_zip_uri(archive))
    with root.batch():
        for i in range(MEMBERS):
            (root / f"d{i % 10}" / f"f{i}.txt").write_bytes(b"y" * 60)
    assert len(derived) <= DERIVATIONS
    members = _members(archive)
    assert members["d3/f13.txt"] == b"y" * 60 and len(members) == MEMBERS + 1


def test_a_thousand_new_members_derive_the_member_list_a_constant_number_of_times(
    tmp_path, derived
):
    archive = _many(tmp_path / "a.zip", 10)
    root = UriPath(_zip_uri(archive))
    with root.batch():
        for i in range(MEMBERS):
            (root / "d1" / f"n{i}.txt").write_bytes(b"n")
    assert len(derived) <= DERIVATIONS
    assert len(_members(archive)) == 11 + MEMBERS


def test_a_sync_that_changes_existing_members_derives_the_member_list_a_constant_number_of_times(
    tmp_path, derived
):
    count = 300
    source = tmp_path / "src"
    for i in range(count):
        (source / f"d{i % 10}").mkdir(parents=True, exist_ok=True)
        (source / f"d{i % 10}" / f"f{i}.txt").write_bytes(b"x" * 50)
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    target = UriPath(_zip_uri(archive, "tree"))
    PathSyncer().sync(LocalPath(source), target)
    for i in range(count):
        (source / f"d{i % 10}" / f"f{i}.txt").write_bytes(b"y" * 60)
    del derived[:]
    PathSyncer().sync(LocalPath(source), target)
    assert len(derived) <= DERIVATIONS
    members = _members(archive)
    assert members["tree/d3/f13.txt"] == b"y" * 60
    assert not [name for name in members if ".pathlib-next-tmp" in name]


def test_a_change_to_a_name_the_archive_spells_twice_derives_it_again_and_is_one_set(
    tmp_path, derived
):
    archive = _write_zip(
        tmp_path / "a.zip",
        [("d/x.txt", b"first"), ("./d/x.txt", b"second"), ("d/y.txt", b"y")],
    )
    one_by_one = _write_zip(
        tmp_path / "b.zip",
        [("d/x.txt", b"first"), ("./d/x.txt", b"second"), ("d/y.txt", b"y")],
    )
    for path, batched in ((one_by_one, False), (archive, True)):
        del derived[:]
        root = UriPath(_zip_uri(path))
        with root.batch() if batched else _Nothing():
            (root / "d" / "y.txt").write_bytes(b"y2")
            (root / "d" / "x.txt").rename("z.txt")
            (root / "d" / "w.txt").write_bytes(b"w")
            (root / "d" / "z.txt").unlink()
    assert _shape(archive) == _shape(one_by_one)
    assert [m for m in _members(archive)] == ["d/y.txt", "d/w.txt"]
    # the rename of the name spelled twice is the one change that derives again
    assert len(derived) <= DERIVATIONS + 1


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- the member list patched change by change is the one derived from the members ----------


def _rebuilt(backend):
    members = backend._ordered()
    return _base._Snapshot(
        type(backend),
        [(name, entry.info) for name, entry in members],
        {name: entry.link for name, entry in members if entry.link is not _NO_LINK},
    )


def _comparable(snapshot):
    """The member list but for the order of the children of a directory, which
    follows the changes made and not the archive."""
    index, spellings, infos, files, implicit, children = _fields(snapshot)
    return (
        index,
        spellings,
        sorted(infos),
        files,
        implicit,
        {name: sorted(names) for name, names in children.items()},
        dict(snapshot.links),
    )


def _listing(root):
    return sorted(
        (str(top.path), sorted(dirs), sorted(files)) for top, dirs, files in root.walk()
    )


def _tree_archive(path):
    members = []
    for n in range(4):
        members.append((f"d{n}/", b""))
        for m in range(3):
            members.append((f"d{n}/f{m}.txt", b"%d%d" % (n, m)))
        members.append((f"d{n}/sub/deep.txt", b"deep"))
    members += [("top.txt", b"top"), ("e/only.txt", b"only"), ("empty/", b"")]
    return _write_zip(path, members)


def _generate(rng, root, steps):
    """`steps` changes picked against `root`, applied to it one at a time (each
    one a rewrite), as `(change, outcome, listing afterwards)`."""
    recorded = []
    for step in range(steps):
        directories = [str(top.path) for top, _d, _f in root.walk()]
        files = [
            f"{top.path}/{name}".lstrip("/")
            for top, _d, names in root.walk()
            for name in names
        ]
        kind = rng.choice(
            [
                "write",
                "write",
                "unlink",
                "unlink",
                "rename",
                "move",
                "mkdir",
                "rmdir",
                "rmtree",
                "rename_dir",
            ]
        )
        directory = rng.choice(directories)
        if kind == "write":
            target = rng.choice(files + [f"{directory}/n{step}.txt".lstrip("/")])
            change = ("write", target, b"w%d" % step)
        elif kind == "unlink" and files:
            change = ("unlink", rng.choice(files))
        elif kind == "rename" and files:
            source = rng.choice(files)
            sibling = rng.choice(files)
            name = sibling.rpartition("/")[2] if rng.random() < 0.3 else f"r{step}.txt"
            change = ("rename", source, name)
        elif kind == "move" and files:
            change = ("move", rng.choice(files), f"{directory}/m{step}.txt".lstrip("/"))
        elif kind == "mkdir":
            change = ("mkdir", f"{directory}/k{step}".lstrip("/"))
        elif kind == "rmdir" and len(directories) > 1:
            change = ("rmdir", rng.choice(directories[1:]))
        elif kind == "rmtree" and len(directories) > 1:
            change = ("rmtree", rng.choice(directories[1:]))
        elif kind == "rename_dir" and len(directories) > 1:
            change = ("rename_dir", rng.choice(directories[1:]), f"s{step}")
        else:
            continue
        outcome = _apply(root, change)
        recorded.append((change, outcome, _listing(root)))
    return recorded


def _apply(root, change):
    kind = change[0]
    try:
        if kind == "write":
            (root / change[1]).write_bytes(change[2])
        elif kind == "unlink":
            (root / change[1]).unlink()
        elif kind == "rename":
            (root / change[1]).rename(change[2])
        elif kind == "move":
            (root / change[1]).rename(root / change[2])
        elif kind == "mkdir":
            (root / change[1]).mkdir()
        elif kind == "rmdir":
            (root / change[1]).rmdir()
        elif kind == "rmtree":
            (root / change[1]).rm(recursive=True)
        elif kind == "rename_dir":
            (root / change[1]).rename(change[2])
    except (OSError, ValueError) as error:
        return type(error).__name__
    return "done"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_run_of_mixed_changes_in_a_batch_is_the_run_one_at_a_time(
    tmp_path, derived, seed
):
    alone = _tree_archive(tmp_path / "alone.zip")
    inside = _tree_archive(tmp_path / "inside.zip")
    recorded = _generate(random.Random(seed), UriPath(_zip_uri(alone)), 120)
    assert sum(1 for _c, outcome, _l in recorded if outcome == "done") > 60
    assert (
        len({change[0] for change, outcome, _l in recorded if outcome == "done"}) >= 7
    )

    del derived[:]
    root = UriPath(_zip_uri(inside))
    backend = root.backend
    checks = 0
    with root.batch():
        for change, outcome, listing in recorded:
            assert _apply(root, change) == outcome, change
            assert _listing(root) == listing, change
            snapshot = backend._batch.snapshot
            if snapshot is not None:
                # deriving the list here, to compare, is the test's own
                checks += 1
                assert _comparable(snapshot) == _comparable(_rebuilt(backend)), change
    assert checks > 50
    assert len(derived) - checks <= DERIVATIONS
    assert _shape(inside) == _shape(alone)


# --- the member list itself --------------------------------------------------------------------


def test_a_member_list_that_loses_one_entry_equals_one_built_without_it():
    from pathlib_next.archive._zipentries import _new_info

    names = [
        "a/b/c.txt",
        "a/b/d.txt",
        "a/e.txt",
        "a/b/",
        "x/",
        "x/y/z/",
        "x/y/z/w.txt",
        "top.txt",
        "p/q/r/s.txt",
        "p/t.txt",
        "m/n/",
        "m/n/o/",
    ]
    entries = {name: _new_info(name, i) for i, name in enumerate(names)}
    snapshot = _base._Snapshot(_ZipBackend, list(entries.items()))
    order = [
        "a/b/c.txt",
        "x/y/z/w.txt",
        "x/y/z/",
        "p/q/r/s.txt",
        "a/b/d.txt",
        "a/b/",
        "m/n/o/",
        "top.txt",
        "x/",
        "a/e.txt",
        "m/n/",
        "p/t.txt",
    ]
    for name in order:
        assert snapshot.drop(name)
        del entries[name]
        rebuilt = _base._Snapshot(_ZipBackend, list(entries.items()))
        assert _comparable(snapshot) == _comparable(rebuilt), name
    assert snapshot.children == {"": {}} and not snapshot.implicit


def test_a_member_list_cannot_drop_a_name_it_holds_under_two_spellings():
    from pathlib_next.archive._zipentries import _new_info

    entries = {"x": _new_info("x", 1), "./x": _new_info("./x", 2)}
    snapshot = _base._Snapshot(_ZipBackend, list(entries.items()))
    before = _comparable(snapshot)
    assert snapshot.drop("./x") is False
    assert snapshot.drop("x") is False
    assert _comparable(snapshot) == before


# --- what the rules of the last commit still say inside a batch ----------------------------------


def test_the_link_rules_hold_on_a_member_list_that_is_patched(tmp_path):
    from test_archive_links import _archive, _root

    root = _root(_archive(tmp_path, "zip"), "zip")
    with root.batch():
        (root / "ln_dir").rename("moved_dir")
        (root / "d" / "in.txt").unlink()
        (root / "d" / "added.txt").write_bytes(b"added")
        assert (root / "moved_dir").is_symlink()
        assert (root / "moved_dir").is_dir()
        assert (root / "moved_dir" / "added.txt").read_bytes() == b"added"
        assert not (root / "moved_dir" / "in.txt").exists()
        tops = sorted(str(top.path) for top, _d, _f in root.walk(follow_symlinks=True))
        assert "moved_dir" in tops and "d/up" not in tops
        assert not (root / "d" / "up").is_dir()
        assert not (root / "moved_dir" / "up").is_dir()
        assert (root / "moved_dir" / "sub" / "deep.txt").read_bytes() == b"deep"
        assert (root / "moved_dir").readlink().path == "d"
    assert (root / "moved_dir").is_symlink()
    assert (root / "moved_dir" / "added.txt").read_bytes() == b"added"
