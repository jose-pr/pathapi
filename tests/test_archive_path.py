"""`pathapi.archive`: `ZipPath`, `TarPath` and `ArchivePath`, the members
of an archive file that is any `Path`, with no URI. How they are built, which
paths share a handle, what is decided and when, what each text form shows, and
the filesystem contracts over a local file and over an in-memory one."""

from __future__ import annotations

import copy
import io
import os
import pickle
import subprocess
import sys
import tarfile
import textwrap
import zipfile

import pytest

from capabilities import stores_question_marks

import pathapi
from pathapi import LocalPath
from pathapi.archive import ArchivePath, LinkTarget, TarPath, ZipPath
from pathapi.mempath import MemPath
from pathapi.testing import PathContract, ReadPathContract


def make_zip(path, members=None):
    members = (
        {"a.txt": b"A", "d/b.txt": b"BB", "d/": b""} if members is None else members
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def make_tar(path, members=None):
    members = {"a.txt": b"A", "d/b.txt": b"BB"} if members is None else members
    with tarfile.open(path, "w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def zip_file(tmp_path):
    return make_zip(tmp_path / "x.zip")


@pytest.fixture
def tar_file(tmp_path):
    return make_tar(tmp_path / "x.tar")


# --- construction ----------------------------------------------------------------------


def test_the_archive_may_be_a_path_a_str_or_a_pathlike(zip_file):
    for archive in (LocalPath(zip_file), str(zip_file), zip_file):
        assert ZipPath("a.txt", archive=archive).read_bytes() == b"A"
    assert isinstance(ZipPath("a.txt", archive=str(zip_file)).archive, LocalPath)


def test_the_archive_may_be_any_path_such_as_one_in_memory(zip_file):
    memory = MemPath("/x.zip")
    memory.write_bytes(zip_file.read_bytes())
    path = ZipPath("d/b.txt", archive=memory)
    assert path.read_bytes() == b"BB"
    assert path.archive is not None and path.archive == memory


def test_an_archive_path_needs_an_archive():
    for cls in (ZipPath, TarPath, ArchivePath):
        with pytest.raises(TypeError):
            cls("a.txt")
        with pytest.raises(TypeError):
            cls("a.txt", archive=1)


def test_building_a_path_reads_nothing(tmp_path):
    missing = tmp_path / "never.dat"
    path = ArchivePath("a/b", archive=missing)
    joined = (path / "c").parent.with_name("z")
    repr(joined), str(joined), pickle.loads(pickle.dumps(joined)), copy.copy(joined)
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        path.read_bytes()


def test_a_join_keeps_the_archive_and_its_handle(zip_file):
    root = ZipPath("", archive=zip_file)
    derived = [
        root / "d",
        root.joinpath("d", "b.txt"),
        (root / "d").parent,
        (root / "a.txt").with_name("c.txt"),
        ZipPath(root, "d"),
        root.with_segments("x"),
        *root.iterdir(),
    ]
    assert all(path.backend is root.backend for path in derived)
    assert all(path.archive == root.archive for path in derived)


def test_paths_built_separately_for_one_file_share_a_handle_and_see_each_write(
    tmp_path,
):
    archive = tmp_path / "w.zip"
    first, second = ZipPath("x.txt", archive=archive), ZipPath(
        "x.txt", archive=str(archive)
    )
    assert first.backend is second.backend
    first.write_bytes(b"one")
    assert second.read_bytes() == b"one"
    second.write_bytes(b"two")
    assert first.read_bytes() == b"two"


def test_an_archive_path_and_a_zip_path_of_one_zip_file_share_a_handle(zip_file):
    generic = ArchivePath("a.txt", archive=zip_file)
    assert generic.read_bytes() == b"A"
    assert generic.backend is ZipPath("a.txt", archive=zip_file).backend


def test_two_in_memory_archives_with_one_name_are_two_archives():
    first, second = MemPath("/x.zip"), MemPath("/x.zip")
    assert first.backend is not second.backend
    for archive, data in ((first, b"1"), (second, b"2")):
        buffer = io.BytesIO()
        make_zip(buffer, {"only.txt": data})
        archive.write_bytes(buffer.getvalue())
    one, two = ZipPath("only.txt", archive=first), ZipPath("only.txt", archive=second)
    assert (one.read_bytes(), two.read_bytes()) == (b"1", b"2")
    assert one.backend is not two.backend


def test_backend_for_shares_a_handle_unless_asked_for_its_own(zip_file):
    shared = ZipPath.backend_for(zip_file)
    assert ZipPath.backend_for(str(zip_file)) is shared
    own = ZipPath.backend_for(zip_file, shared=False)
    assert own is not shared
    assert ZipPath.backend_for(zip_file, shared=False) is not own
    path = ZipPath("a.txt", archive=own)
    assert path.read_bytes() == b"A"
    assert path.backend is own
    assert path.backend is not ZipPath("a.txt", archive=zip_file).backend
    assert ZipPath.backend_for(own) is own


def test_identity_names_a_non_local_archive_in_the_registry(zip_file):
    first, second = MemPath("/x.zip"), MemPath("/elsewhere.zip")
    first.write_bytes(zip_file.read_bytes())
    named = ZipPath.backend_for(first, identity="the-archive")
    assert ZipPath.backend_for(second, identity="the-archive") is named
    assert ZipPath.backend_for(second, identity="another") is not named


# --- text forms ---------------------------------------------------------------------------


def test_str_is_the_member_path_alone(zip_file):
    assert str(ZipPath("d/b.txt", archive=zip_file)) == "d/b.txt"
    assert str(ZipPath("/d//b.txt", archive=zip_file)) == "/d/b.txt"


def test_repr_names_the_class_the_member_and_the_archive(zip_file):
    text = repr(ZipPath("a/b", archive=zip_file))
    assert text == f"ZipPath('a/b', archive={LocalPath(zip_file)!r})"
    assert repr(TarPath("a", archive=zip_file)).startswith("TarPath('a', archive=")
    assert repr(ArchivePath("", archive=zip_file)).startswith(
        "ArchivePath('', archive="
    )


def test_as_uri_is_the_archive_uri_and_the_encoded_member(zip_file):
    uri = ZipPath("d/b c.txt", archive=zip_file).as_uri()
    assert uri == f"zip:{zip_file.as_uri()}!/d/b%20c.txt"
    assert TarPath("x", archive=zip_file).as_uri().startswith("tar:file:")
    assert ArchivePath("x", archive=zip_file).as_uri().startswith("archive:file:")
    assert "!/a%21/b" in ZipPath("a!/b", archive=zip_file).as_uri()


# --- the member is read from the archive root ------------------------------------------


def test_rooted_and_relative_spellings_name_one_member(zip_file):
    spellings = [
        ZipPath(text, archive=zip_file)
        for text in ("d/b.txt", "/d/b.txt", "/d//b.txt", "d/./b.txt", "d/x/../b.txt")
    ]
    assert {path.read_bytes() for path in spellings} == {b"BB"}
    assert len({path._node_key() for path in spellings}) == 1


def test_the_root_is_a_directory_and_lists_its_members(zip_file):
    for text in ("", "/", "."):
        root = ZipPath(text, archive=zip_file)
        assert root.is_dir()
        assert sorted(child.name for child in root.iterdir()) == ["a.txt", "d"]


def test_a_member_that_would_leave_the_archive_is_not_there(zip_file):
    for text in ("../a.txt", "d/../../a.txt"):
        path = ZipPath(text, archive=zip_file)
        assert not path.exists()
        with pytest.raises(FileNotFoundError):
            path.stat()


def test_a_path_of_another_archive_is_another_tree(zip_file, tmp_path):
    other = make_zip(tmp_path / "y.zip")
    mine, theirs = ZipPath("a.txt", archive=zip_file), ZipPath("a.txt", archive=other)
    assert mine == theirs
    assert not mine._same_filesystem(theirs)
    assert mine._node_key()[0] is not theirs._node_key()[0]
    assert mine._same_filesystem(ZipPath("d", archive=zip_file))
    with pytest.raises(ValueError):
        mine.relative_to(theirs)


# --- deciding the format ------------------------------------------------------------------


def test_archive_path_reads_both_formats_whatever_the_extension(tmp_path):
    zipped = make_zip(tmp_path / "z.dat")
    tarred = make_tar(tmp_path / "t.dat")
    by_content = ArchivePath("d/b.txt", archive=zipped), ArchivePath(
        "d/b.txt", archive=tarred
    )
    assert [path.read_bytes() for path in by_content] == [b"BB", b"BB"]
    assert type(by_content[0].backend).__name__ == "_ZipBackend"
    assert type(by_content[1].backend).__name__ == "_TarBackend"


def test_archive_path_decides_from_the_extension_before_the_bytes(tmp_path):
    named = make_zip(tmp_path / "named.zip")
    path = ArchivePath("a.txt", archive=named)
    assert path.read_bytes() == b"A"
    assert type(path.backend).__name__ == "_ZipBackend"


def test_the_format_is_decided_by_the_first_use_not_by_building_the_path(monkeypatch):
    reads = []
    original = MemPath._open

    def counting(self, mode="r", buffering=-1):
        reads.append(self.as_posix())
        return original(self, mode, buffering)

    monkeypatch.setattr(MemPath, "_open", counting)
    path = ArchivePath("a.txt", archive=MemPath("/who.dat"))
    (path / "b").parent.with_name("c")
    assert reads == []


def test_a_failed_read_of_the_archive_decides_nothing(tmp_path):
    target = tmp_path / "later.dat"
    path = ArchivePath("a.txt", archive=target)
    with pytest.raises(FileNotFoundError):
        path.read_bytes()
    make_tar(target)
    assert path.read_bytes() == b"A"
    assert type(path.backend).__name__ == "_TarBackend"


def test_a_zip_path_over_a_tar_file_reports_a_damaged_archive(tar_file):
    with pytest.raises(zipfile.BadZipFile):
        ZipPath("a.txt", archive=tar_file).read_bytes()


# --- writing ------------------------------------------------------------------------------


def test_a_local_zip_is_written_in_place_and_a_memory_one_is_not(tmp_path):
    local = ZipPath("n.txt", archive=tmp_path / "w.zip")
    local.write_bytes(b"new")
    assert zipfile.ZipFile(tmp_path / "w.zip").read("n.txt") == b"new"
    memory = ZipPath("n.txt", archive=MemPath("/w.zip"))
    with pytest.raises(NotImplementedError, match="local"):
        memory.write_bytes(b"new")


def test_a_tar_path_cannot_be_written(tar_file):
    with pytest.raises(NotImplementedError, match="zip"):
        TarPath("n.txt", archive=tar_file).write_bytes(b"x")
    with pytest.raises(NotImplementedError):
        ArchivePath("n.txt", archive=tar_file).write_bytes(b"x")


def test_batch_writes_every_change_in_one_replacement(tmp_path, monkeypatch):
    archive = tmp_path / "b.zip"
    root = ZipPath("", archive=archive)
    root.joinpath("seed").write_bytes(b"s")
    replaced = []
    from pathapi.archive._zip import _ZipBackend

    original = _ZipBackend._replace_outer
    monkeypatch.setattr(
        _ZipBackend,
        "_replace_outer",
        lambda self, fill: (replaced.append(1), original(self, fill))[1],
    )
    with root.batch():
        for number in range(5):
            (root / f"f{number}").write_bytes(b"x")
        assert (root / "f4").read_bytes() == b"x"
    assert len(replaced) == 1
    assert sorted(zipfile.ZipFile(archive).namelist()) == sorted(
        ["seed", *(f"f{n}" for n in range(5))]
    )


def test_rename_stays_inside_one_archive(tmp_path):
    first, second = tmp_path / "1.zip", tmp_path / "2.zip"
    ZipPath("a", archive=first).write_bytes(b"1")
    ZipPath("a", archive=second).write_bytes(b"2")
    with pytest.raises(NotImplementedError):
        ZipPath("a", archive=first).rename(ZipPath("b", archive=second))
    moved = ZipPath("a", archive=first).rename("b")
    assert moved.as_posix() == "b"
    assert zipfile.ZipFile(first).namelist() == ["b"]


def test_a_rename_to_a_name_that_leaves_the_archive_is_refused(tmp_path):
    archive = tmp_path / "e.zip"
    ZipPath("a", archive=archive).write_bytes(b"1")
    with pytest.raises(ValueError, match="escapes the archive root"):
        ZipPath("a", archive=archive).rename("../x")
    assert zipfile.ZipFile(archive).namelist() == ["a"]


def test_a_write_through_a_link_member_is_refused(tmp_path):
    archive = tmp_path / "l.zip"
    with zipfile.ZipFile(archive, "w") as out:
        link = zipfile.ZipInfo("ln")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        out.writestr(link, "target")
        out.writestr("target", "t")
    path = ZipPath("ln", archive=archive)
    assert isinstance(path.readlink(), LinkTarget)
    assert path.readlink().path == "target"
    with pytest.raises(NotImplementedError, match="link"):
        path.write_bytes(b"x")


def test_a_pickle_and_a_copy_find_the_same_handle_again(tmp_path):
    archive = tmp_path / "p.zip"
    path = ZipPath("a", archive=archive)
    path.write_bytes(b"1")
    restored = pickle.loads(pickle.dumps(path))
    assert restored == path and restored.backend is path.backend
    assert copy.copy(path).backend is path.backend
    assert copy.deepcopy(path).backend is path.backend


# --- an archive inside an archive --------------------------------------------------------


def test_an_archive_may_be_a_member_of_another(tmp_path):
    inner = io.BytesIO()
    make_zip(inner, {"deep.txt": b"D"})
    outer = make_zip(tmp_path / "outer.zip", {"inner.zip": inner.getvalue()})
    member = ZipPath("inner.zip", archive=outer)
    nested = ZipPath("deep.txt", archive=member)
    assert nested.read_bytes() == b"D"
    assert ZipPath("deep.txt", archive=ZipPath("inner.zip", archive=outer)).backend is (
        nested.backend
    )
    with pytest.raises(NotImplementedError):
        nested.write_bytes(b"x")


# --- no dependency ------------------------------------------------------------------------


def _run(code, blocked=()):
    prelude = "".join(f"import sys; sys.modules[{name!r}] = None\n" for name in blocked)
    source = os.path.dirname(os.path.dirname(pathapi.__file__))
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [source, *filter(None, [os.environ.get("PYTHONPATH", "")])]
            ),
        },
    )


def test_the_root_exports_the_archive_paths_without_importing_them_early():
    result = _run("""
        import sys
        import pathapi
        print("pathapi.archive" in sys.modules)
        print(pathapi.ZipPath.__module__, pathapi.TarPath is not None,
              pathapi.ArchivePath is not None)
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "False",
        "pathapi.archive._path True True",
    ]


# --- the contracts ------------------------------------------------------------------------


def _walk(tree):
    """`(full path, archive name, is an empty directory)` for what lies below
    `tree`: every file, and every directory that holds nothing."""
    for root, dirs, files in os.walk(tree):
        relative = os.path.relpath(root, tree).replace("\\", "/")
        prefix = "" if relative == "." else relative + "/"
        for name in files:
            yield os.path.join(root, name), prefix + name, False
        for name in dirs:
            full = os.path.join(root, name)
            if not os.listdir(full):
                yield full, prefix + name, True


def _archive_of_tree(fixture_tree, tmp_path_factory, kind):
    """The tree `fixture_tree` holds, as a `kind` archive outside it (one built
    inside the tree it archives lists itself)."""
    target = tmp_path_factory.mktemp(kind) / f"tree.{kind}"
    if kind == "zip":
        with zipfile.ZipFile(target, "w") as archive:
            for full, name, empty in _walk(fixture_tree):
                if empty:
                    archive.writestr(name + "/", "")
                else:
                    archive.write(full, name)
    else:
        with tarfile.open(target, "w") as archive:
            for full, name, _empty in _walk(fixture_tree):
                archive.add(full, arcname=name)
    return target


class TestZipPathContract(PathContract):
    supports_append = False
    supports_question_mark_names = True
    supports_utime = False

    @pytest.fixture
    def root(self, tmp_path_factory, fixture_tree):
        archive = _archive_of_tree(fixture_tree, tmp_path_factory, "zip")
        return ZipPath("", archive=archive)


class TestZipPathInMemoryContract(ReadPathContract):
    @pytest.fixture
    def root(self, tmp_path_factory, fixture_tree):
        archive = _archive_of_tree(fixture_tree, tmp_path_factory, "zip")
        memory = MemPath("/tree.zip")
        memory.write_bytes(archive.read_bytes())
        return ZipPath("", archive=memory)


class TestTarPathContract(ReadPathContract):
    @pytest.fixture
    def root(self, tmp_path_factory, fixture_tree):
        archive = _archive_of_tree(fixture_tree, tmp_path_factory, "tar")
        return TarPath("", archive=archive)


class TestTarPathInMemoryContract(ReadPathContract):
    @pytest.fixture
    def root(self, tmp_path_factory, fixture_tree):
        archive = _archive_of_tree(fixture_tree, tmp_path_factory, "tar")
        memory = MemPath("/tree.tar")
        memory.write_bytes(archive.read_bytes())
        return TarPath("", archive=memory)


class TestArchivePathContract(ReadPathContract):
    @pytest.fixture
    def root(self, tmp_path_factory, fixture_tree):
        archive = _archive_of_tree(fixture_tree, tmp_path_factory, "zip")
        return ArchivePath("", archive=archive)


class _Unkeyed(MemPath):
    """A path that names its tree by nothing but its URI."""

    __slots__ = ()

    def _node_key(self):
        return None

    def as_uri(self):
        return "unkeyed://host" + self.as_posix()


def test_archives_on_outers_that_name_no_node_share_a_handle_by_their_uri():
    first = ZipPath(archive=_Unkeyed("/a.zip"))
    again = ZipPath(archive=_Unkeyed("/a.zip"))
    other = ZipPath(archive=_Unkeyed("/b.zip"))
    assert first.backend is again.backend
    assert first.backend is not other.backend
