"""Symbolic links stored in an archive: a tar `SYMTYPE` member and a zip entry
whose Unix mode says link. A link is listed as a link, stores text, and is
followed inside the archive only: one that leaves it, is absolute or loops
resolves to nothing, and no walk, glob, copy or removal reaches outside the
archive through one. Every archive is built under `tmp_path` with
`tarfile.TarInfo` and `zipfile.ZipInfo`."""

import errno
import hashlib
import io
import os
import posixpath
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath

import pytest

pytest.importorskip("uritools")

from pathlib_next import LocalPath
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.archive._base import MAX_LINKS
from pathlib_next.utils.sync import PathSyncer

#: (name, kind, payload): a file's bytes, or a link's text.
TREE = [
    ("file.txt", "file", b"hello"),
    ("d/", "dir", None),
    ("d/in.txt", "file", b"inner"),
    ("d/sub/deep.txt", "file", b"deep"),
    ("ln_file", "link", "file.txt"),
    ("ln_dir", "link", "d"),
    ("ln_out", "link", "../outside"),
    ("ln_abs", "link", "/etc/passwd"),
    ("loop_a", "link", "loop_b"),
    ("loop_b", "link", "loop_a"),
    ("self", "link", "self"),
    ("d/up", "link", ".."),
    ("d/rel", "link", "../file.txt"),
    ("d/sib", "link", "in.txt"),
    ("d/sub/to_d", "link", "../../d/in.txt"),
    ("ln_missing", "link", "nothing.txt"),
    ("ln_via", "link", "ln_dir/sub/deep.txt"),
    ("ln_dot", "link", "./d/./in.txt"),
]
LINKS = {name: text for name, kind, text in TREE if kind == "link"}
#: One link back to the root: following links descends without end, one
#: branch at a time, until the bound.
LOOP = [
    ("file.txt", "file", b"hello"),
    ("d/", "dir", None),
    ("d/in.txt", "file", b"inner"),
    ("d/up", "link", ".."),
]
FORMATS = ["zip", "tar"]


def _build_tar(path, entries):
    with tarfile.open(path, "w") as archive:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name.rstrip("/"))
            info.mtime = 1_600_000_000
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                info.mode = 0o777
                archive.addfile(info)
            else:
                info.size = len(payload)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(payload))


def _build_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, kind, payload in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 2, 3, 4, 6))
            info.create_system = 3
            if kind == "dir":
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, b"")
            elif kind == "link":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, payload.encode())
            else:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, payload)


def _archive(tmp_path, fmt, entries=TREE, name="links"):
    path = tmp_path / f"{name}.{fmt}"
    (_build_zip if fmt == "zip" else _build_tar)(path, entries)
    return path


def _root(path, fmt):
    return UriPath(f"{fmt}:{path.as_uri()}!/")


@pytest.fixture(params=FORMATS)
def fmt(request):
    return request.param


@pytest.fixture
def root(tmp_path, fmt):
    return _root(_archive(tmp_path, fmt), fmt)


@pytest.fixture
def can_symlink(tmp_path):
    probe = tmp_path / "probe-link"
    try:
        os.symlink("target", probe)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create a symbolic link")
    probe.unlink()


def _link_text(path):
    """The text of a local link; Windows spells its separators with a backslash."""
    return os.readlink(path).replace("\\", "/")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- what a link is --------------------------------------------------------------


def test_a_link_member_is_a_symlink_and_a_file_or_directory_is_not(root):
    for name in LINKS:
        assert (root / name).is_symlink(), name
    for name in ("file.txt", "d", "d/in.txt", "d/sub"):
        assert not (root / name).is_symlink(), name
    assert not root.is_symlink()
    assert not (root / "nothing").is_symlink()


def test_the_non_following_stat_of_a_link_is_a_link_of_the_size_of_its_text(root):
    for name, text in LINKS.items():
        found = (root / name).stat(follow_symlinks=False)
        assert stat.S_ISLNK(found.st_mode), name
        assert found.st_size == len(text.encode()), name
        assert not found.is_dir(), name
        assert (root / name).lstat().st_mode == found.st_mode


def test_readlink_is_the_stored_text(root):
    for name, text in LINKS.items():
        assert (root / name).readlink().path == text, name
    assert (root / "d" / "up").readlink().path == ".."
    # links on the way are followed to reach the one named
    assert (root / "ln_dir" / "rel").readlink().path == "../file.txt"
    # as a path it is normalized, as `PurePosixPath` spells it
    assert (root / "ln_dot").readlink() == PurePosixPath("d/in.txt")
    assert (root / "ln_dot").readlink().parent == PurePosixPath("d")


def test_readlink_of_what_is_not_a_link_is_invalid_and_of_nothing_is_not_found(root):
    for name in ("file.txt", "d", ""):
        with pytest.raises(OSError) as raised:
            (root / name).readlink()
        assert raised.value.errno == errno.EINVAL, name
    with pytest.raises(FileNotFoundError):
        (root / "nothing").readlink()


# --- following inside the archive ----------------------------------------------------


def test_a_link_to_a_file_stats_and_reads_as_the_file(root):
    for name in ("ln_file", "d/rel", "ln_via", "ln_dot", "d/sub/to_d"):
        link = root / name
        assert link.is_file(), name
        assert not link.is_dir(), name
        assert link.stat().st_size == len(link.read_bytes()), name
        assert stat.S_ISREG(link.stat().st_mode), name
    assert (root / "ln_file").read_bytes() == b"hello"
    assert (root / "d" / "rel").read_bytes() == b"hello"
    assert (root / "d" / "sib").read_bytes() == b"inner"
    assert (root / "ln_via").read_bytes() == b"deep"
    assert (root / "ln_dot").read_bytes() == b"inner"
    assert (root / "d" / "sub" / "to_d").read_bytes() == b"inner"
    assert (root / "ln_file").read_text() == "hello"


def test_a_link_to_a_directory_is_one_and_lists_what_it_points_at(root):
    link = root / "ln_dir"
    assert link.is_dir()
    assert not link.is_file()
    assert sorted(child.name for child in link.iterdir()) == [
        "in.txt",
        "rel",
        "sib",
        "sub",
        "up",
    ]
    assert (link / "in.txt").read_bytes() == b"inner"
    assert (link / "sub" / "deep.txt").read_bytes() == b"deep"
    assert (link / "sub").is_dir()
    assert link.stat(follow_symlinks=False).st_mode != link.stat().st_mode
    with pytest.raises(IsADirectoryError):
        link.read_bytes()


def test_a_link_to_a_file_has_no_children_and_lists_nothing(root):
    with pytest.raises(NotADirectoryError):
        list((root / "ln_file").iterdir())
    assert not (root / "ln_file" / "x").exists()


def test_a_name_below_a_link_to_a_file_is_not_found(root):
    with pytest.raises(FileNotFoundError):
        (root / "ln_file" / "x").stat()


def test_a_link_that_leaves_the_archive_or_is_absolute_resolves_to_nothing(root):
    for name in ("ln_out", "ln_abs", "ln_missing"):
        link = root / name
        assert link.is_symlink(), name
        assert not link.exists(), name
        assert not link.is_file() and not link.is_dir(), name
        with pytest.raises(FileNotFoundError) as raised:
            link.stat()
        if name != "ln_missing":
            assert name in raised.value.filename, name
        with pytest.raises(FileNotFoundError):
            link.read_bytes()
        with pytest.raises(FileNotFoundError):
            list(link.iterdir())
    # the link is named, wherever it was met on the way
    broken = root / "ln_out" / "deeper" / "file"
    with pytest.raises(FileNotFoundError) as raised:
        broken.stat()
    assert "ln_out" in raised.value.filename


def test_a_loop_is_eloop_and_names_the_link(root):
    for name in ("loop_a", "loop_b", "self"):
        link = root / name
        assert link.is_symlink(), name
        assert not link.exists(), name
        with pytest.raises(OSError) as raised:
            link.stat()
        assert raised.value.errno == errno.ELOOP, name
        with pytest.raises(OSError) as raised:
            link.read_bytes()
        assert raised.value.errno == errno.ELOOP, name
        assert link.stat(follow_symlinks=False).st_size == len(LINKS[name])


def _chain(links):
    entries = [("file.txt", "file", b"hello")]
    entries += [
        (f"c{number}", "link", f"c{number + 1}" if number < links else "file.txt")
        for number in range(1, links + 1)
    ]
    return entries


def test_at_most_forty_links_are_followed(tmp_path, fmt):
    assert MAX_LINKS == 40
    for links, readable in ((MAX_LINKS, True), (MAX_LINKS + 1, False)):
        root = _root(_archive(tmp_path, fmt, _chain(links), f"chain{links}"), fmt)
        chain = root / "c1"
        assert chain.is_symlink()
        if readable:
            assert chain.read_bytes() == b"hello"
        else:
            with pytest.raises(OSError) as raised:
                chain.read_bytes()
            assert raised.value.errno == errno.ELOOP
            assert not chain.exists()


# --- a tar hard link is a second name, not a symlink --------------------------------


def test_a_tar_hard_link_reads_as_the_file_and_is_no_symlink(tmp_path):
    path = tmp_path / "hard.tar"
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("file.txt")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"hello"))
        info = tarfile.TarInfo("hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "file.txt"
        archive.addfile(info)
    hard = UriPath(f"tar:{path.as_uri()}!/hard")
    assert not hard.is_symlink()
    assert hard.is_file()
    assert hard.stat().st_size == 5
    assert hard.read_bytes() == b"hello"
    with pytest.raises(OSError) as raised:
        hard.readlink()
    assert raised.value.errno == errno.EINVAL


# --- a link whose text cannot be used --------------------------------------------------


def test_a_zip_link_whose_text_is_longer_than_a_path_is_a_link_without_a_target(
    tmp_path,
):
    entries = [("file.txt", "file", b"hello"), ("long", "link", "x" * 5000)]
    root = _root(_archive(tmp_path, "zip", entries), "zip")
    assert (root / "long").is_symlink()
    assert not (root / "long").exists()
    with pytest.raises(OSError) as raised:
        (root / "long").readlink()
    assert raised.value.errno == errno.EIO
    assert (root / "file.txt").read_bytes() == b"hello"


def test_a_zip_link_with_damaged_data_does_not_damage_the_archive(tmp_path):
    entries = [("file.txt", "file", b"hello"), ("ln", "link", "file.txt")]
    path = _archive(tmp_path, "zip", entries)
    data = bytearray(path.read_bytes())
    # a stored entry: flip a byte of the link's text, which breaks its CRC
    position = data.index(b"file.txt", data.index(b"ln"))
    data[position] ^= 0xFF
    path.write_bytes(bytes(data))
    root = _root(path, "zip")
    assert (root / "file.txt").read_bytes() == b"hello"
    assert (root / "ln").is_symlink()
    assert not (root / "ln").exists()
    assert sorted(child.name for child in root.iterdir()) == ["file.txt", "ln"]


def test_a_zip_entry_with_a_link_mode_but_made_elsewhere_is_a_file(tmp_path):
    path = tmp_path / "dos.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("not-a-link")
        info.create_system = 0
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    member = UriPath(f"zip:{path.as_uri()}!/not-a-link")
    assert not member.is_symlink()
    assert member.read_bytes() == b"target"


# --- walking and globbing ----------------------------------------------------------------


def _all_paths(root, **options):
    return [
        (str(top.path), sorted(dirs), sorted(files))
        for top, dirs, files in root.walk(**options)
    ]


def test_walk_lists_each_link_once_as_a_file_and_does_not_enter_it(root):
    walked = dict((top, (dirs, files)) for top, dirs, files in _all_paths(root))
    assert set(walked) == {"", "d", "d/sub"}
    top_dirs, top_files = walked[""]
    assert top_dirs == ["d"]
    for name in ("ln_file", "ln_dir", "ln_out", "ln_abs", "loop_a", "self"):
        assert top_files.count(name) == 1, name
    assert walked["d"] == (["sub"], ["in.txt", "rel", "sib", "up"])
    assert walked["d/sub"] == ([], ["deep.txt", "to_d"])


def test_a_walk_that_follows_links_does_not_enter_a_link_to_an_ancestor(tmp_path, fmt):
    root = _root(_archive(tmp_path, fmt, LOOP, "loop"), fmt)
    seen = []
    for top, dirs, files in root.walk(follow_symlinks=True):
        seen.append((str(top.path), sorted(dirs), sorted(files)))
        assert top.as_uri().split("!/")[0] == root.as_uri().split("!/")[0]
    assert seen == [
        ("", ["d"], ["file.txt"]),
        ("d", [], ["in.txt", "up"]),
    ]


def test_glob_lists_each_link_once_and_does_not_go_through_one(root):
    found = sorted(str(path.path) for path in root.glob("**/*"))
    for name in LINKS:
        assert found.count(name) == 1, name
    assert not [path for path in found if path.startswith("ln_dir/")]
    assert not [path for path in found if "/up/" in path]
    assert "ln_dir" in found and "d/in.txt" in found


def test_glob_that_recurses_through_links_does_not_enter_a_link_to_an_ancestor(
    tmp_path, fmt
):
    root = _root(_archive(tmp_path, fmt, LOOP, "loop"), fmt)
    found = sorted(str(path.path) for path in root.glob("**/*", recurse_symlinks=True))
    assert found == ["d", "d/in.txt", "d/up", "file.txt"]


# --- copying ------------------------------------------------------------------------------


def test_a_link_copied_without_following_is_a_link_of_the_same_text(
    root, tmp_path, can_symlink
):
    out = tmp_path / "out"
    out.mkdir()
    for name, text in LINKS.items():
        copied = out / name.replace("/", "_")
        (root / name).copy(LocalPath(copied), follow_symlinks=False)
        assert copied.is_symlink(), name
        made = _link_text(copied)
        # Windows resolves `.` segments when it stores the text of a link.
        assert made == text or (
            os.name == "nt" and posixpath.normpath(made) == posixpath.normpath(text)
        ), name


def test_a_tree_copied_without_following_links_has_the_links_and_nothing_else(
    root, tmp_path, can_symlink
):
    out = tmp_path / "out"
    root.copy(LocalPath(out), recursive=True, follow_symlinks=False)
    assert sorted(os.listdir(out)) == sorted(
        ["file.txt", "d", *(name for name in LINKS if "/" not in name)]
    )
    assert _link_text(out / "ln_dir") == "d"
    assert _link_text(out / "d" / "up") == ".."
    assert (out / "d" / "in.txt").read_bytes() == b"inner"
    assert not (tmp_path / "outside").exists()


def test_a_tree_copied_with_following_leaves_a_link_to_an_ancestor_out_and_reports_it(
    tmp_path, fmt
):
    root = _root(_archive(tmp_path, fmt, LOOP, "loop"), fmt)
    out = tmp_path / "out"
    errors = []
    (root / "d").copy(LocalPath(out), recursive=True, ignore_error=errors.append)
    assert [getattr(e, "errno", None) for e in errors] == [errno.ELOOP]
    assert sorted(os.listdir(out)) == ["in.txt"]
    assert (out / "in.txt").read_bytes() == b"inner"
    assert sorted(os.listdir(tmp_path)) == [f"loop.{fmt}", "out"]


def test_a_sync_that_keeps_links_makes_links(root, tmp_path, can_symlink):
    out = tmp_path / "out"
    PathSyncer(follow_symlinks=False).sync(root / "d", LocalPath(out))
    assert _link_text(out / "up") == ".."
    assert _link_text(out / "rel") == "../file.txt"
    assert (out / "in.txt").read_bytes() == b"inner"


# --- removing and changing a zip that holds links -----------------------------------------------


@pytest.fixture
def zroot(tmp_path):
    path = _archive(tmp_path, "zip")
    return _root(path, "zip"), path


def _members(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_removing_a_link_removes_the_entry_and_not_what_it_points_at(zroot):
    root, path = zroot
    (root / "ln_dir").rm(recursive=True)
    (root / "ln_file").unlink()
    members = _members(path)
    assert "ln_dir" not in members and "ln_file" not in members
    assert members["d/in.txt"] == b"inner"
    assert members["file.txt"] == b"hello"
    assert (root / "d" / "in.txt").exists()


def test_removing_a_directory_with_a_link_to_its_parent_removes_only_its_entries(
    zroot,
):
    root, path = zroot
    (root / "d").rm(recursive=True)
    members = _members(path)
    assert not [name for name in members if name.startswith("d/")]
    assert members["file.txt"] == b"hello"
    assert "ln_dir" in members


def test_removing_a_tree_with_links_leaves_the_links_when_the_policy_says_so(zroot):
    root, path = zroot
    with pytest.raises(OSError):
        (root / "d").rm(recursive=True, follow_symlinks=None)
    members = _members(path)
    assert "d/up" in members and "d/rel" in members
    assert "d/in.txt" not in members
    assert members["file.txt"] == b"hello"


def test_a_change_to_another_member_keeps_the_links_links(zroot):
    root, path = zroot
    (root / "file.txt").write_bytes(b"changed")
    (root / "d" / "sub" / "deep.txt").unlink()
    assert (root / "ln_file").is_symlink()
    assert (root / "ln_file").read_bytes() == b"changed"
    assert (root / "ln_file").readlink().path == "file.txt"
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("ln_file")
        assert stat.S_ISLNK(info.external_attr >> 16)


def test_a_link_can_be_renamed_and_stays_a_link(zroot):
    root, path = zroot
    (root / "ln_file").rename("renamed")
    assert not (root / "ln_file").exists()
    assert (root / "renamed").is_symlink()
    assert (root / "renamed").readlink().path == "file.txt"
    assert (root / "renamed").read_bytes() == b"hello"


def test_nothing_is_written_through_a_link(zroot):
    root, path = zroot
    before = _digest(path)
    with pytest.raises(NotImplementedError):
        (root / "ln_dir" / "new.txt").write_bytes(b"new")
    with pytest.raises(NotImplementedError):
        (root / "ln_file").write_bytes(b"new")
    with pytest.raises(NotImplementedError):
        (root / "ln_dir" / "newdir").mkdir()
    with pytest.raises(NotImplementedError):
        (root / "ln_dir" / "in.txt").unlink()
    with pytest.raises(NotImplementedError):
        (root / "ln_dir" / "in.txt").rename("moved.txt")
    with pytest.raises(NotImplementedError):
        (root / "file.txt").rename((root / "ln_dir" / "moved.txt"))
    with pytest.raises(FileExistsError):
        (root / "ln_dir").mkdir()
    with pytest.raises(FileExistsError):
        (root / "ln_missing").mkdir()
    with pytest.raises(NotADirectoryError):
        (root / "ln_dir").rmdir()
    assert _digest(path) == before


def test_links_are_read_inside_a_batch_of_changes(zroot):
    root, path = zroot
    before = _digest(path)
    with root.batch():
        (root / "d" / "new.txt").write_bytes(b"new")
        (root / "d" / "in.txt").unlink()
        (root / "ln_file").rename("kept")
        assert (root / "ln_dir" / "new.txt").read_bytes() == b"new"
        assert not (root / "ln_dir" / "in.txt").exists()
        assert (root / "kept").is_symlink()
        assert (root / "kept").readlink().path == "file.txt"
        assert (root / "kept").read_bytes() == b"hello"
        assert (root / "d" / "sib").is_symlink()
        assert not (root / "d" / "sib").exists()
        assert _digest(path) == before
    members = _members(path)
    assert members["d/new.txt"] == b"new"
    assert "d/in.txt" not in members and "ln_file" not in members
    with zipfile.ZipFile(path) as archive:
        assert stat.S_ISLNK(archive.getinfo("kept").external_attr >> 16)
    assert (root / "kept").is_symlink()


def test_an_archive_without_links_resolves_nothing(tmp_path, fmt):
    entries = [("file.txt", "file", b"hello"), ("d/", "dir", None)]
    root = _root(_archive(tmp_path, fmt, entries, "plain"), fmt)
    assert root.backend.snapshot().links == {}
    assert root.backend.snapshot().resolve("d/file.txt") == "d/file.txt"
