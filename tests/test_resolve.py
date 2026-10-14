"""`absolute()`, `resolve()` and `readlink()`/`hardlink_to()` of the generic
protocols: lexical on a backend without links, through `readlink()` on one
that has them, and the same answers as `pathlib` where both can be asked."""

from __future__ import annotations

import errno
import os
import pathlib
import stat
import sys
import zipfile

import pytest

from pathlib_next import LocalPath, MemPath
from pathlib_next.archive import TarPath, ZipPath
from pathlib_next.protocols import Hardlink, Resolve, Symlink, supports

LOOPS_ARE_ERRORS = sys.version_info < (3, 13)


def _zip(path, entries):
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
    return ZipPath(archive=path)


# --- a backend without links: lexical ---------------------------------------


def test_a_memory_path_is_resolved_lexically():
    assert MemPath("/a/../b/./c").resolve().as_posix() == "/b/c"
    assert MemPath("/a/b/../../..").resolve().as_posix() == "/"


def test_a_relative_memory_path_is_absolute_below_its_root():
    here = MemPath("x/y")
    assert here.absolute().as_posix() == "/x/y"
    assert here.resolve().as_posix() == "/x/y"
    assert MemPath("/x").absolute() == MemPath("/x")


def test_the_resolved_path_keeps_the_backend():
    here = MemPath("/a/../b")
    assert here.resolve().backend is here.backend


def test_strict_resolution_of_a_memory_path_needs_every_component():
    here = MemPath("/d/f")
    here.parent.mkdir()
    here.write_text("x")
    assert MemPath("/d/../d/f", backend=here.backend).resolve(strict=True) == here
    with pytest.raises(FileNotFoundError):
        MemPath("/d/missing/f", backend=here.backend).resolve(strict=True)
    assert MemPath("/d/missing/f", backend=here.backend).resolve().as_posix() == (
        "/d/missing/f"
    )


def test_a_memory_path_has_no_links_to_read_or_make():
    assert not supports(MemPath, Symlink)
    assert not supports(MemPath, Hardlink)
    with pytest.raises(NotImplementedError, match="readlink"):
        MemPath("/x").readlink()


# --- a backend with read-only links: an archive -----------------------------

ENTRIES = [
    ("d/", "dir", None),
    ("d/file", "file", b"x"),
    ("d/up", "link", "../d/file"),
    ("ln", "link", "d"),
    ("abs", "link", "/d/file"),
    ("chain", "link", "ln"),
    ("dangling", "link", "nowhere"),
]


@pytest.fixture
def archive(tmp_path):
    return _zip(tmp_path / "links.zip", ENTRIES)


def test_an_archive_reads_links_and_cannot_make_one(archive):
    assert supports(archive, Symlink)
    assert supports(TarPath, Symlink)
    assert (archive / "ln").readlink().as_posix() == "d"


def test_a_link_member_on_the_way_is_followed_through_readlink(archive):
    assert (archive / "ln" / "file").resolve().as_posix() == "/d/file"
    assert (archive / "chain" / "file").resolve().as_posix() == "/d/file"
    assert (archive / "d" / "up").resolve().as_posix() == "/d/file"


def test_an_absolute_link_restarts_at_the_root_of_the_archive(archive):
    assert (archive / "abs").resolve().as_posix() == "/d/file"


def test_a_dangling_link_resolves_to_its_target_unless_strict(archive):
    assert (archive / "dangling").resolve().as_posix() == "/nowhere"
    with pytest.raises(FileNotFoundError):
        (archive / "dangling").resolve(strict=True)


def test_a_relative_archive_path_is_absolute_below_the_root(archive):
    relative = archive.with_segments("ln", "file")
    assert relative.resolve().as_posix() == "/d/file"


def test_forty_one_links_in_a_loop_raise_what_pathlib_raises(tmp_path):
    loop = _zip(tmp_path / "loop.zip", [("a", "link", "b"), ("b", "link", "a")])
    if LOOPS_ARE_ERRORS:
        with pytest.raises(RuntimeError, match="Symlink loop"):
            (loop / "a").resolve()
        with pytest.raises(RuntimeError, match="Symlink loop"):
            (loop / "a").resolve(strict=True)
        return
    with pytest.raises(OSError) as error:
        (loop / "a").resolve(strict=True)
    assert error.value.errno == errno.ELOOP
    assert (loop / "a").resolve().as_posix() in ("/a", "/b")


def test_a_chain_of_forty_links_still_resolves(tmp_path):
    entries = [("end", "file", b"x")]
    entries += [(f"l{n}", "link", f"l{n - 1}" if n else "end") for n in range(40)]
    tree = _zip(tmp_path / "chain.zip", entries)
    assert (tree / "l39").resolve(strict=True).as_posix() == "/end"
    entries.append(("l40", "link", "l39"))
    longer = _zip(tmp_path / "longer.zip", entries)
    if LOOPS_ARE_ERRORS:
        with pytest.raises(RuntimeError):
            (longer / "l40").resolve()
    else:
        with pytest.raises(OSError):
            (longer / "l40").resolve(strict=True)


# --- the same answers as pathlib, on a directory of this machine ------------


@pytest.mark.skipif(os.name == "nt", reason="the generic walk reads POSIX segments")
def test_generic_resolution_agrees_with_pathlib(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "file").write_text("x")
    try:
        os.symlink("d", tmp_path / "ln")
        os.symlink(str(tmp_path / "d" / "file"), tmp_path / "abs")
        os.symlink("../d/file", tmp_path / "d" / "up")
        os.symlink("nowhere", tmp_path / "dangling")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create a symbolic link")
    base = pathlib.Path(os.path.realpath(tmp_path))
    for text in (
        "ln/file",
        "abs",
        "d/up",
        "d/../ln/./file",
        "dangling",
        "d/missing/x",
        "ln/../d",
    ):
        local = LocalPath(base / text)
        assert Resolve.resolve(local) == LocalPath(pathlib.Path(base / text).resolve())
    with pytest.raises(FileNotFoundError):
        Resolve.resolve(LocalPath(base / "dangling"), strict=True)


# --- hard links --------------------------------------------------------------


def test_local_path_makes_a_hard_link(tmp_path):
    source = LocalPath(tmp_path / "a")
    source.write_text("x")
    link = LocalPath(tmp_path / "b")
    link.hardlink_to(source)
    assert os.path.samefile(source, link)
    LocalPath(tmp_path / "c").hardlink_to(str(source))
    assert os.path.samefile(source, tmp_path / "c")
    assert supports(LocalPath, Hardlink)


def test_a_str_target_is_a_path_of_the_same_backend():
    class Linking(MemPath):
        __slots__ = ()
        made = []

        def _hardlink_to(self, target):
            self.made.append((self, target))

    here = Linking("/b")
    here.hardlink_to("/a")
    ((link, target),) = Linking.made
    assert link == here and target == Linking("/a")
    assert type(target) is Linking and target.backend is here.backend
    assert supports(Linking, Hardlink)


def test_a_backend_without_hard_links_says_so():
    with pytest.raises(NotImplementedError, match="_hardlink_to"):
        MemPath("/b").hardlink_to("/a")
