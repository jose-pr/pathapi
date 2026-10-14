"""An `ArchivePath` decides its format on first use, from the bytes it is about
to read anyway, and building, printing, joining or pickling a path reaches
nothing. An outer archive that is not a local file logs what it costs."""

import copy
import errno
import io
import pickle
import tarfile
import zipfile

import pytest

from pathlib_next.archive import ArchivePath, TarPath, ZipPath
from pathlib_next.mempath import MemPath, MemPathBackend


def _zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _tar_bytes(members, mode="w"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Site:
    """Holds `files` (path -> bytes) as in-memory outer archives, logs every
    read of one, and fails the reads while `failing`."""

    def __init__(self, files):
        self.log = []
        self.failing = False
        self._backend = MemPathBackend()
        site = self

        class Served(MemPath):
            __slots__ = ()

            def _open(self, mode="r", buffering=-1):
                if "r" in mode and "+" not in mode:
                    site.log.append(f"GET {self.as_posix()}")
                    if site.failing:
                        raise OSError(errno.EIO, "unavailable")
                return super()._open(mode, buffering)

        self._cls = Served
        for name, data in files.items():
            self.put(name, data)

    def put(self, name, data):
        self.outer(name).write_bytes(data)

    def outer(self, name):
        return self._cls(name, backend=self._backend)


@pytest.fixture
def site():
    served = _Site(
        {
            "/noext": _zip_bytes({"m": b"M", "d/n": b"N"}),
            "/tarnoext": _tar_bytes({"m": b"T"}),
            "/a.zip": _zip_bytes({"m": b"M"}),
        }
    )
    served.log.clear()
    return served


# --- building a path reaches nothing -----------------------------------------------------


@pytest.mark.parametrize("cls", [ArchivePath, ZipPath, TarPath])
def test_building_printing_joining_and_pickling_a_path_read_nothing(site, cls):
    outer = site.outer("/noext")
    path = cls("d/n", archive=outer)
    text = (
        repr(path),
        str(path),
        path.name,
        path.parent,
        path.with_name("x"),
        path / "y",
        path.parent / "z",
        path == cls("d/n", archive=outer),
        hash(path),
        copy.copy(path),
        copy.deepcopy(path),
    )
    assert text[0] and site.log == []


# --- the first use settles the format, and costs one read ---------------------------------


def test_the_first_read_of_an_undecided_zip_reads_the_outer_once(site):
    path = ArchivePath("m", archive=site.outer("/noext"))
    assert path.read_bytes() == b"M"
    assert site.log == ["GET /noext"]
    assert (path.parent / "d" / "n").read_bytes() == b"N"
    assert site.log == ["GET /noext"]


def test_the_first_read_of_an_undecided_tar_reads_the_outer_once(site):
    path = ArchivePath("m", archive=site.outer("/tarnoext"))
    assert path.read_bytes() == b"T"
    assert site.log == ["GET /tarnoext"]


def test_an_undecided_archive_that_is_not_there_costs_one_read(site):
    path = ArchivePath("m", archive=site.outer("/gone"))
    assert not path.exists()
    assert site.log == ["GET /gone"]
    with pytest.raises(FileNotFoundError):
        path.read_bytes()


def test_a_format_named_by_the_class_or_the_extension_is_read_once(site):
    assert ZipPath("m", archive=site.outer("/a.zip")).read_bytes() == b"M"
    assert site.log == ["GET /a.zip"]
    site.log.clear()
    assert ArchivePath("m", archive=site.outer("/a.zip")).read_bytes() == b"M"
    assert site.log == ["GET /a.zip"]


def test_a_failure_to_read_the_outer_decides_nothing_for_the_next_try(site):
    outer = site.outer("/noext")
    site.failing = True
    path = ArchivePath("m", archive=outer)
    with pytest.raises(OSError):
        path.read_bytes()
    site.failing = False
    assert path.read_bytes() == b"M"
    assert path.backend is ZipPath("m", archive=outer).backend


def test_an_undecided_local_archive_is_read_by_what_it_holds(tmp_path):
    archive = tmp_path / "blob"
    archive.write_bytes(_zip_bytes({"m": b"local"}))
    path = ArchivePath("m", archive=archive)
    assert path.read_bytes() == b"local"
    assert path.backend is ZipPath("m", archive=archive).backend


# --- a path pickled and loaded again reaches the same handle --------------------------


@pytest.mark.parametrize("cls", [ZipPath, ArchivePath])
@pytest.mark.parametrize("protocol", [2, pickle.HIGHEST_PROTOCOL])
def test_a_loaded_path_shares_the_handle_the_original_holds(tmp_path, cls, protocol):
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"m": b"M"}))
    path = cls("m", archive=archive)
    again = pickle.loads(pickle.dumps(path, protocol))
    assert again == path and str(again) == str(path)
    assert again.backend is path.backend
    assert copy.deepcopy(path).backend is path.backend
    assert again.read_bytes() == b"M"


def test_a_tar_path_and_a_nested_archive_path_share_their_handles_after_a_pickle(
    tmp_path,
):
    outer = tmp_path / "o.tar"
    outer.write_bytes(_tar_bytes({"inner.zip": _zip_bytes({"x": b"X"}), "m": b"M"}))
    plain = TarPath("m", archive=outer)
    nested = ZipPath("x", archive=TarPath("inner.zip", archive=outer))
    for path in (plain, nested):
        again = pickle.loads(pickle.dumps(path))
        assert again == path
        assert again.backend is path.backend
    assert pickle.loads(pickle.dumps(nested)).read_bytes() == b"X"


# --- a held remote archive is read again on request -------------------------------------


def test_refresh_makes_an_archive_that_changed_readable(site):
    outer = site.outer("/a.zip")
    path = ZipPath("m", archive=outer)
    other = ZipPath(archive=outer)
    assert path.read_bytes() == b"M"
    site.put("/a.zip", _zip_bytes({"m": b"CHANGED", "new": b"N"}))
    site.log.clear()
    # The bytes of an outer that is not a local file are kept while any path
    # to it lives.
    assert path.read_bytes() == b"M" and not (other / "new").exists()
    path.refresh()
    assert site.log == []  # forgetting costs nothing by itself
    assert path.read_bytes() == b"CHANGED"
    assert (other / "new").read_bytes() == b"N"  # every path to it shares the refresh
    assert site.log == ["GET /a.zip"]


def test_refresh_of_an_undecided_archive_nothing_read_yet_reads_nothing(site):
    path = ArchivePath("m", archive=site.outer("/noext"))
    path.refresh()
    assert site.log == []
    assert path.read_bytes() == b"M"
    site.put("/noext", _zip_bytes({"m": b"CHANGED"}))
    path.refresh()
    assert path.read_bytes() == b"CHANGED"


def test_refresh_of_a_local_archive_changes_nothing(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(_zip_bytes({"m": b"M"}))
    path = ZipPath("m", archive=archive)
    assert path.read_bytes() == b"M"
    path.refresh()
    assert path.read_bytes() == b"M"


@pytest.mark.parametrize("cls", [ArchivePath, ZipPath])
def test_two_paths_built_for_one_archive_are_on_one_filesystem(tmp_path, cls):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("a.txt", "A")
    first = cls(archive=archive) / "a.txt"
    second = cls(archive=archive) / "a.txt"
    other_spelling = ZipPath(archive=archive) / "a.txt"

    assert first._same_filesystem(second) and second._same_filesystem(first)
    assert first._same_filesystem(other_spelling)
    assert pickle.loads(pickle.dumps(first))._same_filesystem(first)
    with pytest.raises(OSError):
        first.copy(second, overwrite=True)
    assert first.read_bytes() == b"A"


def test_paths_of_two_archives_are_on_two_filesystems(tmp_path):
    paths = []
    for name in ("a.zip", "b.zip"):
        archive = tmp_path / name
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("a.txt", "A")
        paths.append(ArchivePath(archive=archive) / "a.txt")

    assert not paths[0]._same_filesystem(paths[1])


def test_an_archive_that_cannot_be_read_is_on_no_other_filesystem(tmp_path):
    missing = ArchivePath(archive=tmp_path / "missing") / "a.txt"
    again = ArchivePath(archive=tmp_path / "missing") / "a.txt"

    assert not missing._same_filesystem(again)
