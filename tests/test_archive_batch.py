"""`ArchiveUri.batch()`: the changes made to a zip inside the block reach the
file in one replacement when it ends, every path of the archive reads them
meanwhile, and an exception leaves the file as it was. `copy(recursive=True)`
into a zip and `PathSyncer` onto one enter a batch by themselves."""

import contextlib
import errno
import hashlib
import os
import threading
import zipfile

import pytest

pytest.importorskip("uritools")

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath
from pathlib_next.uri import UriPath
from pathlib_next.archive import _zipentries as zip_module
from pathlib_next.archive._zip import _ZipBackend
from pathlib_next.utils.sync import PathSyncer


def _zip_uri(path, inner=""):
    return f"zip:{path.as_uri()}!/{inner}"


def _tar_uri(path, inner=""):
    return f"tar:{path.as_uri()}!/{inner}"


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return path


def _members(path):
    """`{name: bytes}` of an archive that passes `testzip()`."""
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        return {info.filename: zf.read(info) for info in zf.infolist()}


@pytest.fixture
def rewrites(monkeypatch):
    """One entry per replacement of an archive file."""
    calls = []
    original = _ZipBackend._replace_outer

    def counted(self, fill):
        calls.append(1)
        return original(self, fill)

    monkeypatch.setattr(_ZipBackend, "_replace_outer", counted)
    return calls


@pytest.fixture
def archive(tmp_path):
    return _write_zip(
        tmp_path / "a.zip",
        [
            ("keep.txt", b"keep"),
            ("d/one.txt", b"one"),
            ("d/two.txt", b"two"),
            ("e/only.txt", b"only"),
        ],
    )


def _tree(base, files=300, directories=10):
    base.mkdir()
    for index in range(files):
        directory = base / f"d{index % directories}"
        directory.mkdir(exist_ok=True)
        (directory / f"f{index}.txt").write_bytes(b"content %d" % index)
    return base


def _tree_members(base):
    return {
        path.relative_to(base).as_posix(): path.read_bytes()
        for path in base.rglob("*")
        if path.is_file()
    }


# --- a batch is one replacement ------------------------------------------------


def test_every_change_in_a_batch_is_written_by_one_replacement(archive, rewrites):
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "new.txt").write_bytes(b"new")
        (root / "keep.txt").write_bytes(b"kept again")
        (root / "d" / "two.txt").unlink()
        (root / "f").mkdir()
        (root / "f" / "g.txt").write_bytes(b"g")
        (root / "d" / "one.txt").rename("uno.txt")
    assert len(rewrites) == 1
    assert _members(archive) == {
        "keep.txt": b"kept again",
        "d/uno.txt": b"one",
        "e/only.txt": b"only",
        "new.txt": b"new",
        "f/": b"",
        "f/g.txt": b"g",
    }


def test_a_change_outside_a_batch_is_one_replacement_each(archive, rewrites):
    root = UriPath(_zip_uri(archive))
    (root / "new.txt").write_bytes(b"new")
    (root / "keep.txt").write_bytes(b"again")
    (root / "d" / "two.txt").unlink()
    assert len(rewrites) == 3


def test_a_batch_that_changes_nothing_does_not_touch_the_file(archive, rewrites):
    before = _digest(archive)
    mtime = os.stat(archive).st_mtime_ns
    root = UriPath(_zip_uri(archive))
    with root.batch():
        assert (root / "keep.txt").read_bytes() == b"keep"
        assert not (root / "none.txt").exists()
    assert rewrites == []
    assert _digest(archive) == before
    assert os.stat(archive).st_mtime_ns == mtime


def test_a_batch_inside_a_batch_joins_it(archive, rewrites):
    root = UriPath(_zip_uri(archive))
    before = _digest(archive)
    with root.batch():
        with root.batch():
            (root / "inner.txt").write_bytes(b"inner")
        assert _digest(archive) == before
        assert rewrites == []
        (root / "outer.txt").write_bytes(b"outer")
    assert len(rewrites) == 1
    assert {"inner.txt", "outer.txt"} <= set(_members(archive))


def test_a_batch_made_through_another_path_of_the_archive_is_the_same_batch(
    archive, rewrites
):
    first = UriPath(_zip_uri(archive))
    second = UriPath(_zip_uri(archive))
    with first.batch():
        with second.batch():
            (second / "a.txt").write_bytes(b"a")
        (first / "b.txt").write_bytes(b"b")
    assert len(rewrites) == 1
    assert {"a.txt", "b.txt"} <= set(_members(archive))


# --- what a reader sees inside ------------------------------------------------


def test_the_file_is_untouched_until_the_block_ends(archive, tmp_path):
    before = _digest(archive)
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "new.txt").write_bytes(b"new")
        (root / "d" / "one.txt").unlink()
        (root / "e" / "only.txt").rename("moved.txt")
        assert _digest(archive) == before
        assert [p.name for p in tmp_path.iterdir()] == ["a.zip"]
    assert _digest(archive) != before


def test_every_path_of_the_archive_reads_the_changes_made_in_the_block(archive):
    root = UriPath(_zip_uri(archive))
    other = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "new.txt").write_bytes(b"new")
        (root / "keep.txt").write_bytes(b"kept again")
        (root / "d" / "one.txt").unlink()
        (root / "e" / "only.txt").rename("moved.txt")
        (root / "made").mkdir()
        assert (other / "new.txt").read_bytes() == b"new"
        assert (other / "keep.txt").read_text() == "kept again"
        assert (other / "keep.txt").stat().st_size == len(b"kept again")
        assert not (other / "d" / "one.txt").exists()
        assert (other / "e" / "moved.txt").read_bytes() == b"only"
        assert not (other / "e" / "only.txt").exists()
        assert (other / "made").is_dir()
        assert sorted(p.name for p in other.iterdir()) == [
            "d",
            "e",
            "keep.txt",
            "made",
            "new.txt",
        ]
        walked = {str(top.path): sorted(files) for top, _dirs, files in other.walk()}
        assert walked["d"] == ["two.txt"]
        assert walked["e"] == ["moved.txt"]
    assert sorted(p.name for p in root.iterdir()) == [
        "d",
        "e",
        "keep.txt",
        "made",
        "new.txt",
    ]


def test_a_member_written_in_the_block_reports_the_stat_it_has_afterwards(archive):
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "new.txt").write_bytes(b"new bytes")
        (root / "keep.txt").write_bytes(b"x")
        (root / "e" / "only.txt").rename("moved.txt")
        inside = {
            name: (root / name).stat()
            for name in ("new.txt", "keep.txt", "e/moved.txt")
        }
    for name, stat in inside.items():
        after = (root / name).stat()
        assert (after.st_size, after.st_mode, after.st_mtime) == (
            stat.st_size,
            stat.st_mode,
            stat.st_mtime,
        ), name
        assert after.is_dir() == stat.is_dir()


def test_a_member_read_in_the_block_that_is_in_the_file_is_read_from_it(archive):
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "new.txt").write_bytes(b"new")
        assert (root / "d" / "two.txt").read_bytes() == b"two"
        (root / "d").rename("renamed")
        assert (root / "renamed" / "two.txt").read_bytes() == b"two"
        with pytest.raises(FileNotFoundError):
            (root / "d" / "two.txt").read_bytes()


def test_an_existing_member_written_in_the_block_keeps_its_place(archive):
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "d" / "one.txt").write_bytes(b"ONE")
        (root / "added.txt").write_bytes(b"added")
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == [
            "keep.txt",
            "d/one.txt",
            "d/two.txt",
            "e/only.txt",
            "added.txt",
        ]


def test_a_member_larger_than_the_spool_is_held_and_written_whole(archive, monkeypatch):
    monkeypatch.setattr(zip_module, "MEMBER_SPOOL_BYTES", 64)
    big = os.urandom(5000)
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "big.bin").write_bytes(big)
        (root / "other.bin").write_bytes(big[::-1])
        assert (root / "big.bin").read_bytes() == big
    assert _members(archive)["big.bin"] == big
    assert _members(archive)["other.bin"] == big[::-1]


# --- an exception, and a changed file ---------------------------------------------


def test_an_exception_in_the_block_leaves_the_archive_byte_for_byte(
    archive, rewrites, tmp_path
):
    before = _digest(archive)
    root = UriPath(_zip_uri(archive))
    with pytest.raises(RuntimeError, match="stop"):
        with root.batch():
            (root / "new.txt").write_bytes(b"new")
            (root / "d" / "one.txt").unlink()
            (root / "e").rename("moved")
            raise RuntimeError("stop")
    assert _digest(archive) == before
    assert rewrites == []
    assert [p.name for p in tmp_path.iterdir()] == ["a.zip"]
    assert not (root / "new.txt").exists()
    assert (root / "d" / "one.txt").read_bytes() == b"one"
    assert (root / "e" / "only.txt").exists()
    # the archive takes the next change like any other
    (root / "after.txt").write_bytes(b"after")
    assert len(rewrites) == 1


def test_an_exception_in_a_joined_block_discards_the_outer_changes_too(
    archive, rewrites
):
    before = _digest(archive)
    root = UriPath(_zip_uri(archive))
    with pytest.raises(RuntimeError):
        with root.batch():
            (root / "outer.txt").write_bytes(b"outer")
            with root.batch():
                (root / "inner.txt").write_bytes(b"inner")
                raise RuntimeError("stop")
    assert _digest(archive) == before
    assert rewrites == []


def test_a_batch_that_fails_while_writing_leaves_the_archive_byte_for_byte(
    archive, monkeypatch, tmp_path
):
    before = _digest(archive)
    root = UriPath(_zip_uri(archive))

    def refuse(tmp, original, src, steps):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(_ZipBackend, "_emit", staticmethod(refuse))
    with pytest.raises(OSError) as raised:
        with root.batch():
            (root / "new.txt").write_bytes(b"new")
    assert raised.value.errno == errno.ENOSPC
    assert _digest(archive) == before
    assert [p.name for p in tmp_path.iterdir()] == ["a.zip"]
    assert not (root / "new.txt").exists()


def test_a_failed_copy_into_a_zip_leaves_the_archive_byte_for_byte(
    archive, tmp_path, monkeypatch
):
    source = _tree(tmp_path / "src", files=12, directories=3)
    before = _digest(archive)
    writes = []
    original = _ZipBackend.write_member

    def fail_on_the_fifth(self, path, data):
        writes.append(path)
        if len(writes) == 5:
            raise OSError(errno.EIO, "refused")
        return original(self, path, data)

    monkeypatch.setattr(_ZipBackend, "write_member", fail_on_the_fifth)
    with pytest.raises(OSError):
        LocalPath(source).copy(UriPath(_zip_uri(archive, "tree")), recursive=True)
    assert len(writes) == 5
    assert _digest(archive) == before


def test_a_file_changed_by_someone_else_during_the_block_is_not_overwritten(
    archive, tmp_path
):
    root = UriPath(_zip_uri(archive))
    with pytest.raises(OSError) as raised:
        with root.batch():
            (root / "mine.txt").write_bytes(b"mine")
            theirs = _write_zip(tmp_path / "theirs.zip", [("theirs.txt", b"theirs")])
            os.replace(theirs, archive)
    assert raised.value.errno == errno.EBUSY
    assert _members(archive) == {"theirs.txt": b"theirs"}
    assert [p.name for p in tmp_path.iterdir()] == ["a.zip"]


# --- an archive that is not there yet ---------------------------------------------


def test_a_batch_creates_a_missing_archive_when_it_ends(tmp_path):
    archive = tmp_path / "new.zip"
    root = UriPath(_zip_uri(archive))
    with root.batch():
        assert not root.exists()
        (root / "a.txt").write_bytes(b"a")
        (root / "d").mkdir()
        (root / "d" / "b.txt").write_bytes(b"b")
        assert not archive.exists()
        assert (root / "d" / "b.txt").read_bytes() == b"b"
    assert _members(archive) == {"a.txt": b"a", "d/": b"", "d/b.txt": b"b"}
    assert [p.name for p in tmp_path.iterdir()] == ["new.zip"]


def test_a_batch_that_fails_leaves_a_missing_archive_missing(tmp_path):
    archive = tmp_path / "new.zip"
    root = UriPath(_zip_uri(archive))
    with pytest.raises(RuntimeError):
        with root.batch():
            (root / "a.txt").write_bytes(b"a")
            raise RuntimeError("stop")
    assert list(tmp_path.iterdir()) == []


def test_an_archive_that_appears_during_a_batch_that_would_create_it_is_kept(tmp_path):
    archive = tmp_path / "new.zip"
    root = UriPath(_zip_uri(archive))
    with pytest.raises(FileExistsError):
        with root.batch():
            (root / "a.txt").write_bytes(b"a")
            _write_zip(archive, [("theirs.txt", b"theirs")])
    assert _members(archive) == {"theirs.txt": b"theirs"}


def test_a_batch_of_a_file_that_is_not_an_archive_fails_on_the_first_change(tmp_path):
    archive = tmp_path / "plain.zip"
    archive.write_bytes(b"not a zip at all")
    root = UriPath(_zip_uri(archive))
    with pytest.raises(zipfile.BadZipFile):
        with root.batch():
            (root / "a.txt").write_bytes(b"a")
    assert archive.read_bytes() == b"not a zip at all"


# --- what cannot be batched ----------------------------------------------------


def test_a_tar_has_no_batch(tmp_path):
    import tarfile

    path = tmp_path / "a.tar"
    with tarfile.open(path, "w"):
        pass
    with pytest.raises(NotImplementedError):
        UriPath(_tar_uri(path)).batch()


def test_an_archive_inside_an_archive_has_no_batch(archive):
    nested = UriPath(f"zip:zip:{archive.as_uri()}!/keep.txt!/x")
    with pytest.raises(NotImplementedError):
        nested.batch()


def test_copying_a_tree_into_a_tar_is_refused_as_before(tmp_path):
    import tarfile

    path = tmp_path / "a.tar"
    with tarfile.open(path, "w"):
        pass
    source = _tree(tmp_path / "src", files=3, directories=1)
    with pytest.raises(NotImplementedError):
        LocalPath(source).copy(UriPath(_tar_uri(path, "tree")), recursive=True)


# --- another thread -------------------------------------------------------------


def test_another_thread_waits_for_the_block_and_then_reads_the_result(archive):
    root = UriPath(_zip_uri(archive))
    seen = []

    def reader():
        seen.append((UriPath(_zip_uri(archive)) / "new.txt").read_bytes())

    thread = threading.Thread(target=reader)
    with root.batch():
        (root / "new.txt").write_bytes(b"new")
        thread.start()
        thread.join(0.3)
        assert thread.is_alive()
        assert seen == []
    thread.join(30)
    assert seen == [b"new"]


# --- the tree operations ---------------------------------------------------------


def test_copying_a_tree_into_a_zip_is_one_replacement(tmp_path, rewrites):
    source = _tree(tmp_path / "src")
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    target = UriPath(_zip_uri(archive, "tree"))
    LocalPath(source).copy(target, recursive=True)
    assert len(rewrites) == 1
    members = _members(archive)
    assert members.pop("seed.txt") == b"seed"
    assert {
        name[len("tree/") :]: data
        for name, data in members.items()
        if not name.endswith("/")
    } == _tree_members(source)
    assert (target / "d3" / "f3.txt").read_bytes() == b"content 3"


def test_copying_a_tree_into_a_new_zip_creates_it_without_replacing_it(
    tmp_path, rewrites
):
    source = _tree(tmp_path / "src", files=30, directories=3)
    archive = tmp_path / "new.zip"
    LocalPath(source).copy(UriPath(_zip_uri(archive, "tree")), recursive=True)
    members = _members(archive)
    assert {
        n[len("tree/") :]: d for n, d in members.items() if not n.endswith("/")
    } == _tree_members(source)
    assert rewrites == []


def test_a_tree_copied_into_a_zip_equals_the_tree_copied_member_by_member(
    tmp_path, monkeypatch
):
    source = _tree(tmp_path / "src", files=40, directories=4)
    batched = _write_zip(tmp_path / "batched.zip", [("seed.txt", b"seed")])
    single = _write_zip(tmp_path / "single.zip", [("seed.txt", b"seed")])
    LocalPath(source).copy(UriPath(_zip_uri(batched, "tree")), recursive=True)
    monkeypatch.setattr(_ZipBackend, "batch", contextlib.nullcontext)
    LocalPath(source).copy(UriPath(_zip_uri(single, "tree")), recursive=True)
    assert list(_members(batched).items()) == list(_members(single).items())


def test_syncing_a_tree_onto_a_zip_is_one_replacement(tmp_path, rewrites):
    source = _tree(tmp_path / "src")
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    target = UriPath(_zip_uri(archive, "tree"))
    PathSyncer().sync(LocalPath(source), target)
    assert len(rewrites) == 1
    members = _members(archive)
    assert {
        name[len("tree/") :]: data
        for name, data in members.items()
        if name.startswith("tree/") and not name.endswith("/")
    } == _tree_members(source)
    assert not [name for name in members if ".pathlib-next-tmp" in name]


def test_a_sync_that_finds_nothing_to_do_does_not_rewrite_the_zip(tmp_path, rewrites):
    source = _tree(tmp_path / "src", files=20, directories=2)
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    target = UriPath(_zip_uri(archive, "tree"))
    PathSyncer().sync(LocalPath(source), target)
    done = len(rewrites)
    before = _digest(archive)
    PathSyncer().sync(LocalPath(source), target)
    assert len(rewrites) == done
    assert _digest(archive) == before


def test_a_sync_that_adds_changes_and_removes_is_one_replacement(tmp_path, rewrites):
    source = _tree(tmp_path / "src", files=20, directories=2)
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    target = UriPath(_zip_uri(archive, "tree"))
    PathSyncer().sync(LocalPath(source), target)
    (source / "d0" / "f0.txt").write_bytes(b"changed")
    (source / "d1" / "f1.txt").unlink()
    (source / "d1" / "added.txt").write_bytes(b"added")
    del rewrites[:]
    PathSyncer(remove_missing=True).sync(LocalPath(source), target)
    assert len(rewrites) == 1
    members = {
        name[len("tree/") :]: data
        for name, data in _members(archive).items()
        if name.startswith("tree/") and not name.endswith("/")
    }
    assert members == _tree_members(source)
    assert members["d0/f0.txt"] == b"changed"


def test_a_dry_run_sync_onto_a_zip_opens_no_batch(tmp_path, rewrites, monkeypatch):
    source = _tree(tmp_path / "src", files=5, directories=1)
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    opened = []
    original = _ZipBackend.batch
    monkeypatch.setattr(
        _ZipBackend, "batch", lambda self: opened.append(1) or original(self)
    )
    PathSyncer().sync(
        LocalPath(source), UriPath(_zip_uri(archive, "tree")), dry_run=True
    )
    assert opened == []
    assert rewrites == []


def test_removing_a_tree_in_a_batch_with_other_changes_is_one_replacement(
    archive, rewrites
):
    root = UriPath(_zip_uri(archive))
    with root.batch():
        (root / "d").rm(recursive=True)
        (root / "new.txt").write_bytes(b"new")
        assert not (root / "d").exists()
    assert len(rewrites) == 1
    assert _members(archive) == {
        "keep.txt": b"keep",
        "e/only.txt": b"only",
        "new.txt": b"new",
    }


# --- the same result as a rewrite for each change ---------------------------------


def _script(root):
    """A run of changes that exercises every kind of pending state: a name
    written twice, a directory kept alive by its marker, a rename over a
    member and of a whole directory, a member spelled two ways."""
    (root / "new.txt").write_bytes(b"new")
    (root / "keep.txt").write_bytes(b"kept again")
    (root / "d" / "two.txt").unlink()
    (root / "d" / "one.txt").unlink()
    assert (root / "d").is_dir()
    (root / "f").mkdir()
    (root / "f" / "g.txt").write_bytes(b"g")
    (root / "f" / "g.txt").rename("h.txt")
    (root / "e").rename("moved")
    (root / "moved" / "later.txt").write_bytes(b"later")
    (root / "dup.txt").rename("keep.txt")
    (root / "spelled.txt").write_bytes(b"spelled again")
    (root / "spelled.txt").unlink()
    (root / "keep.txt").write_bytes(b"final keep")
    with (root / "moved" / "only.txt").open("r+b") as stream:
        stream.write(b"+")
    (root / "gone").mkdir()
    (root / "gone").rmdir()
    (root / "d").rmdir()
    (root / "tree").rm(recursive=True)


def _script_archive(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.comment = b"the comment"
        for index, (name, data) in enumerate(
            [
                ("keep.txt", b"keep"),
                ("dup.txt", b"dup"),
                ("./spelled.txt", b"spelled"),
                ("d/one.txt", b"one"),
                ("d/two.txt", b"two"),
                ("e/only.txt", b"only"),
                ("tree/a/x.txt", b"x"),
                ("tree/a/y.txt", b"y"),
                ("tree/z.txt", b"z"),
            ]
        ):
            info = zipfile.ZipInfo(name, (2020, 1 + index, 2, 3, 4, 5))
            info.compress_type = (
                zipfile.ZIP_STORED if index % 2 else zipfile.ZIP_DEFLATED
            )
            info.external_attr = (0o640 << 16) | index
            info.comment = b"c%d" % index
            zf.writestr(info, data)
    return path


def _shape(path):
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        return zf.comment, [
            (
                info.filename,
                zf.read(info),
                info.compress_type,
                info.external_attr,
                info.comment,
            )
            for info in zf.infolist()
        ]


def test_a_script_of_changes_gives_the_same_archive_in_a_batch_and_out_of_one(
    tmp_path,
):
    single = _script_archive(tmp_path / "single.zip")
    batched = _script_archive(tmp_path / "batched.zip")
    _script(UriPath(_zip_uri(single)))
    root = UriPath(_zip_uri(batched))
    with root.batch():
        _script(root)
    assert _shape(batched) == _shape(single)
    assert _shape(batched)[0] == b"the comment"


def test_the_listing_in_the_block_is_the_listing_after_each_change(tmp_path):
    single = _script_archive(tmp_path / "single.zip")
    batched = _script_archive(tmp_path / "batched.zip")
    alone = UriPath(_zip_uri(single))
    inside = UriPath(_zip_uri(batched))

    def listing(root):
        return sorted(
            (str(top.path), sorted(dirs), sorted(files))
            for top, dirs, files in root.walk()
        )

    with inside.batch():
        for change in (
            lambda root: (root / "new.txt").write_bytes(b"new"),
            lambda root: (root / "d" / "two.txt").unlink(),
            lambda root: (root / "d" / "one.txt").unlink(),
            lambda root: (root / "e").rename("moved"),
            lambda root: (root / "tree" / "a").rename("b"),
            lambda root: (root / "tree").rm(recursive=True),
        ):
            change(alone)
            change(inside)
            assert listing(inside) == listing(alone)
    assert listing(inside) == listing(alone)


# --- the hook ---------------------------------------------------------------------


class _Recording(MemPath):
    log = None

    def _write_batch(self):
        log = type(self).log

        @contextlib.contextmanager
        def block():
            log.append(("enter", str(self)))
            try:
                yield
            finally:
                log.append(("exit", str(self)))

        return block()

    def _mkdir(self, mode):
        type(self).log.append(("write", str(self)))
        return super()._mkdir(mode)

    def _open(self, mode="r", buffering=-1):
        if "w" in mode:
            type(self).log.append(("write", str(self)))
        return super()._open(mode, buffering)


def _memory_source():
    source = MemPath("/src")
    source.mkdir()
    (source / "a").mkdir()
    (source / "a" / "x.txt").write_bytes(b"x")
    (source / "y.txt").write_bytes(b"y")
    return source


def test_the_default_hook_does_nothing(tmp_path):
    for path in (LocalPath(tmp_path), MemPath("/m")):
        with path._write_batch() as entered:
            assert entered is None


def test_a_recursive_copy_writes_only_inside_the_hook_of_its_target():
    _Recording.log = []
    target = _Recording("/dst")
    _memory_source().copy(target, recursive=True)
    log = _Recording.log
    assert log[0] == ("enter", "/dst")
    assert log[-1] == ("exit", "/dst")
    depth = 0
    for event, _name in log:
        if event == "enter":
            depth += 1
        elif event == "exit":
            depth -= 1
        else:
            assert depth > 0, log
    assert depth == 0
    assert sum(1 for event, _ in log if event == "write") == 4
    assert (target / "a" / "x.txt").read_bytes() == b"x"


def test_a_single_file_copy_does_not_enter_the_hook():
    _Recording.log = []
    source = MemPath("/one")
    source.write_bytes(b"1")
    source.copy(_Recording("/two"))
    assert [event for event, _ in _Recording.log if event != "write"] == []


def test_a_sync_enters_the_hook_of_its_target_once_and_a_dry_run_never():
    _Recording.log = []
    PathSyncer().sync(_memory_source(), _Recording("/dst"))
    log = _Recording.log
    assert [e for e in log if e[0] != "write"] == [("enter", "/dst"), ("exit", "/dst")]
    assert log[0][0] == "enter" and log[-1][0] == "exit"
    _Recording.log = []
    PathSyncer().sync(_memory_source(), _Recording("/dry"), dry_run=True)
    assert _Recording.log == []


# --- the member list of a batch ----------------------------------------------------


def _fields(snapshot):
    return (
        dict(snapshot.index),
        {name: list(raws) for name, raws in snapshot.spellings.items()},
        dict(snapshot.infos),
        set(snapshot.files),
        set(snapshot.implicit),
        {name: list(children) for name, children in snapshot.children.items()},
    )


def test_a_member_list_given_one_more_entry_equals_one_built_with_it():
    from pathlib_next.archive._snapshot import _Snapshot
    from pathlib_next.archive._zipentries import _new_info

    names = [
        "a/b/c.txt",
        "a/d.txt",
        "a/b/",
        "x/",
        "x/y/z/",
        "x/y/z/w.txt",
        "top.txt",
        "./dot.txt",
        "a/b/c.txt",
        "p/q.txt",
        "../escape.txt",
        "a/b/c.txt/under-a-file",
        "p/",
        "y/y/y/y.txt",
    ]
    snapshot = _Snapshot(_ZipBackend, [])
    entries = {}
    for name in names:
        info = _new_info(name, len(entries))
        assert snapshot.add(name, info)
        entries[name] = info
        assert _fields(snapshot) == _fields(
            _Snapshot(_ZipBackend, list(entries.items()))
        )
    assert snapshot.add("dot.txt", _new_info("dot.txt", 1)) is False
    assert _fields(snapshot) == _fields(_Snapshot(_ZipBackend, list(entries.items())))


def test_copying_a_tree_into_a_zip_derives_the_member_list_a_bounded_number_of_times(
    tmp_path, monkeypatch
):
    from pathlib_next.archive import _snapshot as _base

    source = _tree(tmp_path / "src", files=200, directories=10)
    archive = _write_zip(tmp_path / "a.zip", [("seed.txt", b"seed")])
    built = []
    original = _base._Snapshot.__init__

    def counted(self, *args, **kwargs):
        built.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_base._Snapshot, "__init__", counted)
    LocalPath(source).copy(UriPath(_zip_uri(archive, "tree")), recursive=True)
    assert len(built) <= 4
