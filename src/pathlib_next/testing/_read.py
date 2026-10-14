"""The contracts of a pure path and of a read-only path."""

from __future__ import annotations

import pickle
import stat as _stat
import pytest

from ..protocols import supports

from ._fixtures import DIRECTORY_ERRORS, _rel


class PurePathContract:
    """Contract tests for pure path (logical) operations, not requiring any I/O."""

    def test_pure_basics(self, root):
        p = root / "dir" / "file.txt"
        assert p.name == "file.txt"
        assert p.suffix == ".txt"
        assert p.stem == "file"

        # parent and parents
        assert p.parent.name == "dir"
        assert len(p.parents) >= 2
        assert p.parents[0].name == "dir"

    def test_pure_joinpath_truediv(self, root):
        p = root / "a"
        assert (p / "b").name == "b"
        assert p.joinpath("b", "c").name == "c"

    def test_pure_match(self, root):
        p = root / "dir" / "file.txt"
        assert p.match("*.txt")
        assert not p.match("*.py")


class ReadPathContract(PurePathContract):
    """Contract tests for read-only path operations.

    Subclasses must provide a `root` fixture pointing to a fresh directory
    pre-populated with the standard tree -- see `populate_fixture_tree()` and
    `FIXTURE_TREE`:

    - a.txt (content: "a")
    - b.py (content: "b")
    - .hidden.txt (content: "hidden")
    - sub/c.py (content: "c")
    - sub/nested/d.py (content: "d")
    - empty_dir/

    Capability attributes (all default True; set one False only for a
    documented gap, and the affected tests skip): `supports_listing`,
    `supports_empty_directories`, `distinguishes_file_types`,
    `supports_pickle`.
    """

    supports_listing = True
    supports_empty_directories = True
    distinguishes_file_types = True
    supports_pickle = True

    def _require(self, capability):
        if not getattr(self, capability):
            pytest.skip(f"{type(self).__name__}.{capability} is False")

    def _support(self, root, *protocols):
        """Skip when the class under test does not implement a protocol the
        test needs; the skip names it."""
        for protocol in protocols:
            if not supports(root, protocol):
                pytest.skip(
                    f"{type(root).__name__} does not support {protocol.__name__}"
                )

    def test_exists_and_types(self, root):
        assert root.exists()
        assert root.is_dir()

        a = root / "a.txt"
        assert a.exists()
        assert a.is_file()
        assert not a.is_dir()

        sub = root / "sub"
        assert sub.exists()
        assert sub.is_dir()
        assert not sub.is_file()

        assert not (root / "nonexistent").exists()
        assert not (root / "nonexistent").is_file()
        assert not (root / "nonexistent").is_dir()

    def test_a_pickled_path_is_equal_to_the_original(self, root):
        # A worker process receives a path by pickle. The copy must be the
        # same value: equal, hashing alike, and for a URI path placed on the
        # same filesystem as the original.
        self._require("supports_pickle")
        path = root / "a.txt"
        again = pickle.loads(pickle.dumps(path))
        assert type(again) is type(path)
        assert again == path
        assert hash(again) == hash(path)
        if hasattr(path, "_supplied_backend"):
            assert again._same_filesystem(path)
            assert path._same_filesystem(again)

    def test_read_text_and_bytes(self, root):
        assert (root / "a.txt").read_text() == "a"
        assert (root / "a.txt").read_bytes() == b"a"
        assert (root / "sub" / "c.py").read_text() == "c"
        assert (root / "sub" / "nested" / "d.py").read_bytes() == b"d"

    def test_open_read_modes(self, root):
        f = root / ".hidden.txt"
        with f.open() as fh:
            assert fh.read() == "hidden"
        with f.open("r") as fh:
            assert fh.read() == "hidden"
        with f.open("rt") as fh:
            assert fh.read() == "hidden"
        with f.open("rb") as fh:
            assert fh.read() == b"hidden"

    def test_iterdir_lists_children(self, root):
        self._require("supports_listing")
        names = {p.name for p in root.iterdir()}
        assert names == {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir"}
        if self.supports_empty_directories:
            assert list((root / "empty_dir").iterdir()) == []

    def test_iterdir_missing_raises_file_not_found(self, root):
        self._require("supports_listing")
        with pytest.raises(FileNotFoundError):
            list((root / "nonexistent").iterdir())

    def test_iterdir_file_raises_not_a_directory(self, root):
        self._require("supports_listing")
        self._require("distinguishes_file_types")
        with pytest.raises(NotADirectoryError):
            list((root / "a.txt").iterdir())

    def test_stat(self, root):
        st = (root / "a.txt").stat()
        assert st.st_size == 1
        assert isinstance(st.st_mtime, (int, float))
        # A second, longer file: a backend that reports one size for all.
        assert (root / ".hidden.txt").stat().st_size == len("hidden")

    def test_stat_tells_a_directory_from_a_file(self, root):
        self._require("distinguishes_file_types")
        assert _stat.S_ISREG((root / "a.txt").stat().st_mode)
        assert not _stat.S_ISDIR((root / "a.txt").stat().st_mode)
        assert _stat.S_ISDIR((root / "sub").stat().st_mode)
        assert not _stat.S_ISREG((root / "sub").stat().st_mode)

    def test_listing_metadata_agrees_with_stat(self, root):
        # `_scandir()` carries the metadata `walk()`, `rm()` and `PathSyncer` trust without
        # a `stat()` per entry; `None` means unknown, never a different answer, and a
        # stat with `size_known` False answers the type alone.
        self._require("supports_listing")
        listed = dict(root._scandir())
        assert set(listed) == {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir"}
        for name, entry in listed.items():
            if entry is None:
                continue
            real = (root / name).stat()
            assert _stat.S_ISDIR(entry.st_mode) == _stat.S_ISDIR(real.st_mode), name
            if not _stat.S_ISDIR(real.st_mode) and getattr(entry, "size_known", True):
                assert entry.st_size == real.st_size, name

    def test_stat_missing_raises_file_not_found(self, root):
        with pytest.raises(FileNotFoundError):
            (root / "nonexistent").stat()

    def test_read_missing_raises_file_not_found(self, root):
        with pytest.raises(FileNotFoundError):
            (root / "nonexistent.txt").read_bytes()
        with pytest.raises(FileNotFoundError):
            (root / "nonexistent" / "x.txt").read_text()

    def test_read_directory_raises(self, root):
        self._require("distinguishes_file_types")
        with pytest.raises(DIRECTORY_ERRORS):
            (root / "sub").read_bytes()

    def test_nothing_exists_below_a_file(self, root):
        below = root / "a.txt" / "x"
        assert below.exists() is False
        assert below.is_file() is False
        assert below.is_dir() is False
        with pytest.raises((FileNotFoundError, NotADirectoryError)):
            below.read_bytes()
        with pytest.raises((FileNotFoundError, NotADirectoryError)):
            below.stat()

    def test_partial_reads_continue_where_the_last_stopped(self, root):
        with (root / ".hidden.txt").open("rb") as fh:
            assert fh.read(2) == b"hi"
            assert fh.read(3) == b"dde"
            assert fh.read() == b"n"
            assert fh.read() == b""

    def test_glob(self, root):
        self._require("supports_listing")
        assert {_rel(root, p) for p in root.glob("*.py")} == {"b.py"}
        # pathlib matches hidden names with "*".
        assert {_rel(root, p) for p in root.glob("*")} == {
            "a.txt",
            "b.py",
            ".hidden.txt",
            "sub",
            "empty_dir",
        }
        assert {_rel(root, p) for p in root.glob("sub/*.py")} == {"sub/c.py"}
        # A trailing separator selects directories only -- with `native=False`,
        # the one rule for every interpreter. The default follows the running
        # one, and pathlib ignored a trailing separator before 3.11.
        assert {_rel(root, p) for p in root.glob("*/", native=False)} == {
            "sub",
            "empty_dir",
        }

    def test_glob_recursive(self, root):
        self._require("supports_listing")
        expected = {"b.py", "sub/c.py", "sub/nested/d.py"}
        assert {_rel(root, p) for p in root.glob("**/*.py")} == expected
        assert {_rel(root, p) for p in root.rglob("*.py")} == expected

    def test_glob_missing_or_file_parent_selects_nothing(self, root):
        self._require("supports_listing")
        assert list((root / "nonexistent").glob("*")) == []
        assert list((root / "a.txt").glob("*")) == []
        assert list(root.glob("*.nonexistent")) == []

    def test_walk(self, root):
        self._require("supports_listing")
        expected = {
            ".": (["empty_dir", "sub"], [".hidden.txt", "a.txt", "b.py"]),
            "sub": (["nested"], ["c.py"]),
            "sub/nested": ([], ["d.py"]),
            "empty_dir": ([], []),
        }
        top_down = list(root.walk())
        actual = {
            _rel(root, d): (sorted(dirs), sorted(files)) for d, dirs, files in top_down
        }
        if not self.supports_empty_directories:
            del actual["empty_dir"], expected["empty_dir"]
        assert actual == expected
        assert _rel(root, top_down[0][0]) == "."

        bottom_up = [_rel(root, d) for d, _, _ in root.walk(top_down=False)]
        assert sorted(bottom_up) == sorted(_rel(root, d) for d, _, _ in top_down)
        assert bottom_up[-1] == "."
        assert bottom_up.index("sub/nested") < bottom_up.index("sub")
