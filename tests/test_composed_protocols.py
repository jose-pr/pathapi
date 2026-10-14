"""A read-only backend is four mixins: `Pathname`, `Stat`, `Listing` and
`BinaryOpen` over a dict, with no `Path` in its bases. The generic helpers
read it through those protocols alone."""

from __future__ import annotations

import hashlib
import io

import pytest

from pathlib_next import FileStat, MemPath, Path, Pathname, checksum, glob, sync
from pathlib_next.protocols import (
    BinaryOpen,
    Copy,
    Listing,
    Stat,
    Symlink,
    capabilities,
    supports,
)

FILES = {
    "a.txt": b"a",
    "b.py": b"b",
    ".hidden.txt": b"hidden",
    "sub": {"c.py": b"c", "nested": {"d.py": b"d"}},
    "empty_dir": {},
}


class DictPath(Pathname, Listing, Stat, BinaryOpen):
    """The paths of a tree held in nested dicts: names are `str`, a `dict`
    is a directory and `bytes` is a file."""

    __slots__ = ("_segments", "_tree")

    def __init__(self, *segments, tree=None):
        path = ""
        for segment in segments:
            if isinstance(segment, DictPath):
                text = segment.as_posix()
                tree = segment._tree if tree is None else tree
            else:
                text = str(segment)
            path = text if text.startswith("/") or not path else f"{path}/{text}"
        names = [name for name in path.split("/") if name and name != "."]
        self._segments = ("", *names) if names else ("", "")
        self._tree = tree if tree is not None else {}

    @property
    def segments(self):
        return self._segments

    @property
    def parts(self):
        return self._segments, self._tree

    @property
    def parent(self):
        if len(self._segments) <= 2:
            return self
        return self.with_segments(*self._segments[:-1])

    def with_segments(self, *segments):
        return type(self)("/".join(segments), tree=self._tree)

    def as_uri(self):
        return "dict:" + self.as_posix()

    def _node(self):
        node = self._tree
        for name in self._segments:
            if not name:
                continue
            if not isinstance(node, dict) or name not in node:
                raise FileNotFoundError(2, "No such file or directory", str(self))
            node = node[name]
        return node

    def stat(self, *, follow_symlinks=True):
        node = self._node()
        if isinstance(node, dict):
            return FileStat(is_dir=True)
        return FileStat(is_dir=False, st_size=len(node), st_mtime=1.0)

    def iterdir(self):
        node = self._node()
        if not isinstance(node, dict):
            raise NotADirectoryError(20, "Not a directory", str(self))
        for name in node:
            yield self.with_segments(*self._segments, name)

    def _open(self, mode="r", buffering=-1):
        node = self._node()
        if mode != "r" or isinstance(node, dict):
            raise PermissionError(1, "read-only", str(self))
        return io.BytesIO(node)


@pytest.fixture
def root():
    return DictPath("/", tree=FILES)


def test_the_class_is_composed_without_path():
    assert Path not in DictPath.__mro__
    assert capabilities(DictPath) == {Stat, Listing, BinaryOpen}
    assert not supports(DictPath, Copy)
    assert not supports(DictPath, Symlink)


def test_glob_rglob_and_walk_read_it(root):
    assert sorted(p.name for p in root.glob("*.txt")) == [".hidden.txt", "a.txt"]
    assert sorted(p.name for p in root.rglob("*.py")) == ["b.py", "c.py", "d.py"]
    walked = {top.as_posix(): (sorted(d), sorted(f)) for top, d, f in root.walk()}
    assert walked["/sub"] == (["nested"], ["c.py"])
    assert walked["/"] == (["empty_dir", "sub"], [".hidden.txt", "a.txt", "b.py"])


def test_the_glob_function_reads_it(root):
    found = glob.glob(root / "sub" / "**" / "*.py", recursive=True)
    assert sorted(p.as_posix() for p in found) == ["/sub/c.py", "/sub/nested/d.py"]


def test_text_and_bytes_are_read(root):
    assert (root / "a.txt").read_text() == "a"
    assert (root / "sub" / "c.py").read_bytes() == b"c"
    assert (root / "sub").is_dir() and (root / "a.txt").is_file()
    assert not (root / "missing").exists()


def test_checksums_stream_from_it(root):
    path = root / "sub" / "nested" / "d.py"
    assert checksum.md5(path) == hashlib.md5(b"d", usedforsecurity=False).hexdigest()
    assert checksum.sha256(path) == hashlib.sha256(b"d").hexdigest()
    assert checksum.native(path) is None


def test_it_is_a_source_of_a_sync_into_a_memory_path(root):
    target = MemPath("/dst")
    sync.PathSyncer().sync(root, target)
    assert target.is_dir()
    assert (target / "a.txt").read_bytes() == b"a"
    assert (target / ".hidden.txt").read_bytes() == b"hidden"
    assert (target / "sub" / "nested" / "d.py").read_bytes() == b"d"
    assert (target / "empty_dir").is_dir()


def test_a_second_sync_changes_nothing(root):
    target = MemPath("/dst")
    syncer = sync.PathSyncer()
    syncer.sync(root, target)
    before = (target / "a.txt").stat().st_mtime
    syncer.sync(root, target)
    assert (target / "a.txt").stat().st_mtime == before


def test_a_contract_test_skips_for_a_protocol_the_class_lacks_and_names_it(root):
    from pathlib_next.protocols import Create, Listing
    from pathlib_next.testing import ReadPathContract

    contract = ReadPathContract()
    contract._support(root, Listing)
    with pytest.raises(pytest.skip.Exception, match="DictPath does not support Create"):
        contract._support(root, Create)
