"""The contract of a path that can be written."""

from __future__ import annotations

import pytest

from ..protocols import Create, Move, Remove, Utime

from ._fixtures import (
    DIRECTORY_ERRORS,
    NOT_EMPTY_ERRNOS,
    QUESTION_MARK_NAMES,
    UNUSUAL_NAMES,
    _payload,
)
from ._read import ReadPathContract


class PathContract(ReadPathContract):
    """Mixin of filesystem-contract tests every writable Path implementation
    (a custom `Path` subclass) must satisfy.

    Subclasses must provide a `root` fixture pointing to a fresh, writable,
    function-scoped directory pre-populated with the standard tree (see
    `populate_fixture_tree()`). Tests create fixed names under it without
    cleaning up.

    Capability attributes (all default True except
    `supports_question_mark_names`; set one False only for a documented gap):
    `supports_rename`, `supports_append`, `supports_exclusive_create`,
    `enforces_directory_hierarchy`, `supports_mkdir`, `supports_delete`,
    `supports_move`, `supports_unusual_names`, `supports_utime`, and
    `supports_question_mark_names` (default False).
    """

    supports_rename = True
    supports_append = True
    supports_exclusive_create = True
    enforces_directory_hierarchy = True
    supports_mkdir = True
    supports_delete = True
    supports_move = True
    supports_unusual_names = True
    supports_question_mark_names = False
    supports_utime = True

    def test_mkdir_and_is_dir(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        d = root / "new_dir"
        assert not d.exists()
        d.mkdir()
        assert d.is_dir()
        assert not d.is_file()

    def test_mkdir_existing_raises_file_exists(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        with pytest.raises(FileExistsError):
            (root / "sub").mkdir()
        (root / "sub").mkdir(exist_ok=True)
        # exist_ok never covers a file in the way.
        with pytest.raises(FileExistsError):
            (root / "a.txt").mkdir(exist_ok=True)
        assert (root / "a.txt").read_text() == "a"

    def test_mkdir_missing_parent_raises_file_not_found(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        self._require("enforces_directory_hierarchy")
        with pytest.raises(FileNotFoundError):
            (root / "missing_parent" / "child").mkdir()
        assert not (root / "missing_parent").exists()

    def test_write_read_text_roundtrip(self, root):
        f = root / "write_f.txt"
        f.write_text("hello")
        assert f.exists()
        assert f.is_file()
        assert not f.is_dir()
        assert f.read_text() == "hello"

    def test_write_read_bytes_roundtrip(self, root):
        f = root / "write_f.bin"
        f.write_bytes(b"\x00\x01hello")
        assert f.read_bytes() == b"\x00\x01hello"

    def test_every_byte_value_survives_a_multi_chunk_round_trip(self, root):
        # Larger than any one transfer block, with every byte value and runs
        # of CR/LF: a transport that translates line ends, keeps seven bits,
        # or stops at its first chunk changes the bytes or the length.
        payload = _payload()
        f = root / "payload.bin"
        f.write_bytes(payload)
        assert f.stat().st_size == len(payload)
        assert f.read_bytes() == payload

        g = root / "payload_streamed.bin"
        with g.open("wb") as fh:
            fh.write(payload[:7])
            fh.write(payload[7:70000])
            fh.write(payload[70000:])
        assert g.read_bytes() == payload

        with f.open("rb") as fh:
            assert fh.read(100) == payload[:100]
            assert fh.read(70000) == payload[100:70100]
            assert fh.read() == payload[70100:]

    def test_names_with_url_characters_are_stored_and_listed_as_written(self, root):
        self._require("supports_unusual_names")
        self._round_trip_names(root, UNUSUAL_NAMES)

    def test_names_with_a_question_mark_are_stored_and_listed_as_written(self, root):
        self._require("supports_question_mark_names")
        self._round_trip_names(root, QUESTION_MARK_NAMES)

    def _round_trip_names(self, root, names):
        for index, name in enumerate(names):
            f = root / name
            assert f.name == name
            assert not f.exists(), name
            text = f"content {index}"
            f.write_text(text)
            assert f.exists() and f.is_file(), name
            assert f.read_text() == text, name
            assert f.stat().st_size == len(text), name
        if self.supports_listing:
            expected = {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir", *names}
            assert {p.name for p in root.iterdir()} == expected
        if self.supports_rename:
            for index, name in enumerate(names):
                target = root / f"renamed {name}"
                (root / name).rename(target)
                assert not (root / name).exists(), name
                assert target.read_text() == f"content {index}", name
        if self.supports_delete:
            for name in names:
                path = root / (f"renamed {name}" if self.supports_rename else name)
                path.unlink()
                assert not path.exists(), name

    def test_write_missing_parent_raises_file_not_found(self, root):
        self._require("enforces_directory_hierarchy")
        with pytest.raises(FileNotFoundError):
            (root / "missing_parent" / "y.txt").write_text("y")
        assert not (root / "missing_parent").exists()

    def test_write_directory_raises(self, root):
        self._require("enforces_directory_hierarchy")
        with pytest.raises(DIRECTORY_ERRORS):
            (root / "sub").write_bytes(b"x")
        assert (root / "sub").is_dir()
        assert (root / "sub" / "c.py").read_text() == "c"

    def test_open_write_truncates(self, root):
        f = root / "write_trunc.txt"
        f.write_text("long content")
        with f.open("w") as fh:
            fh.write("x")
        assert f.read_text() == "x"
        with f.open("wt") as fh:
            fh.write("yz")
        assert f.read_text() == "yz"
        with f.open("wb") as fh:
            fh.write(b"\x00")
        assert f.read_bytes() == b"\x00"

    def test_open_append(self, root):
        self._require("supports_append")
        f = root / "append.txt"
        with f.open("a") as fh:  # creates a missing file
            fh.write("a")
        assert f.read_text() == "a"
        with f.open("a") as fh:
            fh.write("b")
        with f.open("ab") as fh:
            fh.write(b"c")
        assert f.read_text() == "abc"

    def test_open_exclusive_create(self, root):
        self._require("supports_exclusive_create")
        f = root / "exclusive.txt"
        with f.open("x") as fh:
            fh.write("new")
        assert f.read_text() == "new"
        with pytest.raises(FileExistsError):
            f.open("x")
        with pytest.raises(FileExistsError):
            f.open("xb")
        assert f.read_text() == "new"

    def test_unlink(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        f = root / "write_unlink.txt"
        f.write_text("x")
        assert f.exists()
        f.unlink()
        assert not f.exists()

    def test_unlink_missing_raises_then_missing_ok(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        f = root / "missing_unlink.txt"
        with pytest.raises(FileNotFoundError):
            f.unlink()
        f.unlink(missing_ok=True)

    def test_unlink_directory_raises(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        with pytest.raises(DIRECTORY_ERRORS):
            (root / "empty_dir").unlink()
        assert (root / "empty_dir").is_dir()

    def test_rmdir_requires_empty(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        self._require("supports_delete")
        self._support(root, Remove)
        d = root / "new_rmdir"
        d.mkdir()
        (d / "f.txt").write_text("x")
        with pytest.raises(OSError) as info:
            d.rmdir()
        assert info.value.errno in NOT_EMPTY_ERRNOS
        assert (d / "f.txt").read_text() == "x"
        (d / "f.txt").unlink()
        d.rmdir()
        assert not d.exists()

    def test_rmdir_missing_raises_file_not_found(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        with pytest.raises(FileNotFoundError):
            (root / "missing_rmdir").rmdir()

    def test_rmdir_file_raises_not_a_directory(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        with pytest.raises(NotADirectoryError):
            (root / "a.txt").rmdir()
        assert (root / "a.txt").read_text() == "a"

    def test_rm_recursive(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        self._require("supports_delete")
        self._support(root, Remove)
        d = root / "new_rm_rec"
        d.mkdir()
        (d / "f.txt").write_text("x")
        (d / "sub_rec").mkdir()
        (d / "sub_rec" / "g.txt").write_text("y")
        d.rm(recursive=True)
        assert not d.exists()

    def test_rm_recursive_accepts_the_follow_policies(self, root):
        # `rm()` takes `follow_symlinks=` and `follow_binds=` on every
        # implementation; a tree holding no link or binding comes out the
        # same under each of them.
        self._require("supports_mkdir")
        self._support(root, Create)
        self._require("supports_delete")
        self._support(root, Remove)
        policies = (
            {"follow_symlinks": False, "follow_binds": False},
            {"follow_symlinks": True, "follow_binds": True},
            {"follow_symlinks": None, "follow_binds": None},
            {"follow_symlinks": lambda path: False, "follow_binds": lambda path: None},
        )
        for index, keywords in enumerate(policies):
            d = root / f"new_rm_policy_{index}"
            d.mkdir()
            (d / "f.txt").write_text("x")
            (d / "sub_pol").mkdir()
            (d / "sub_pol" / "g.txt").write_text("y")
            d.rm(recursive=True, **keywords)
            assert not d.exists()

    def test_rm_rejects_a_policy_that_is_not_one(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        self._require("supports_delete")
        self._support(root, Remove)
        d = root / "new_rm_bad_policy"
        d.mkdir()
        (d / "f.txt").write_text("x")
        with pytest.raises(ValueError):
            d.rm(recursive=True, ignore_error=True, follow_symlinks="always")
        assert (d / "f.txt").read_text() == "x"

    def test_rm_non_recursive_directory_requires_empty(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        with pytest.raises(OSError) as info:
            (root / "sub").rm()
        assert info.value.errno in NOT_EMPTY_ERRNOS
        assert (root / "sub" / "c.py").exists()
        (root / "empty_dir").rm()
        assert not (root / "empty_dir").exists()

    def test_rm_missing_ok(self, root):
        self._require("supports_delete")
        self._support(root, Remove)
        with pytest.raises(FileNotFoundError):
            (root / "missing_rm").rm()
        (root / "missing_rm").rm(missing_ok=True)

    def test_one_error_predicate_means_the_same_to_rm_and_copy(self, root):
        # `(error, path) -> bool`: True skips the entry, False raises, and a predicate that
        # raises ends the call with its own exception. `rm()` is offered a missing path,
        # `copy()` a child whose target is a directory.
        self._require("supports_mkdir")
        self._support(root, Create)

        class Stop(Exception):
            pass

        def attempt(call):
            try:
                call()
            except Stop:
                return "stop"
            except OSError:
                return "error"
            return "returned"

        outcomes = {}
        for decision in ("swallow", "decline", "raise"):
            offered = []

            def predicate(error, path):
                offered.append((isinstance(error, OSError), path.name))
                if decision == "raise":
                    raise Stop()
                return decision == "swallow"

            source = root / f"predicate_{decision}_src"
            target = root / f"predicate_{decision}_dst"
            source.mkdir()
            (source / "clash.txt").write_text("source")
            (target / "clash.txt").mkdir(parents=True)
            (target / "clash.txt" / "kept.txt").write_text("target")

            removed = attempt(
                lambda: (root / f"predicate_{decision}_gone").rm(ignore_error=predicate)
            )
            copied = attempt(
                lambda: source.copy(
                    target, recursive=True, overwrite=True, on_error=predicate
                )
            )
            outcomes[decision] = (removed, copied)
            assert offered == [
                (True, f"predicate_{decision}_gone"),
                (True, "clash.txt"),
            ], decision
            assert (target / "clash.txt" / "kept.txt").read_text() == "target"

        assert outcomes == {
            "swallow": ("returned", "returned"),
            "decline": ("error", "error"),
            "raise": ("stop", "stop"),
        }

    def test_copy_preserves_source(self, root):
        src = root / "a.txt"
        dst = root / "dst_copy.txt"
        src.copy(dst)
        assert dst.read_text() == "a"
        assert src.exists()

    def test_copy_existing_target_raises_without_overwrite(self, root):
        src = root / "a.txt"
        dst = root / "dst_copy_existing.txt"
        dst.write_text("existing")
        with pytest.raises(FileExistsError):
            src.copy(dst)
        assert dst.read_text() == "existing"
        if self.supports_delete:  # replacing a file removes it first
            src.copy(dst, overwrite=True)
            assert dst.read_text() == "a"

    def test_copy_missing_source_raises_file_not_found(self, root):
        dst = root / "dst_copy_missing.txt"
        with pytest.raises(FileNotFoundError):
            (root / "nonexistent.txt").copy(dst)
        assert not dst.exists()

    def test_copy_onto_itself_raises_and_keeps_content(self, root):
        # A second spelling of the same file, built separately: truncating
        # the "target" before reading the source would destroy the only copy.
        src = root / "a.txt"
        with pytest.raises(OSError):
            src.copy(root / "a.txt", overwrite=True)
        assert src.read_text() == "a"

    def test_same_filesystem_holds_within_one_root(self, root):
        # `copy()`, `move()` and `PathSyncer` only compare two paths that
        # `_same_filesystem()` places together; paths derived from one root
        # always are. A type fronting several hosts or stores overrides it.
        child = root / "a.txt"
        if type(child) is type(root):
            assert root._same_filesystem(child)
            assert child._same_filesystem(root)
        assert child._same_filesystem(root / "sub" / "c.py")

    def test_copy_recursive(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        dst = root / "sub_copy"
        (root / "sub").copy(dst, recursive=True)
        assert (dst / "c.py").read_text() == "c"
        assert (dst / "nested" / "d.py").read_text() == "d"
        assert (root / "sub" / "nested" / "d.py").exists()

        (root / "sub" / "c.py").write_text("changed")
        with pytest.raises(FileExistsError):
            (root / "sub").copy(dst, recursive=True)
        assert (dst / "c.py").read_text() == "c"
        if self.supports_delete:  # replacing a file removes it first
            (root / "sub").copy(dst, recursive=True, overwrite=True)
            assert (dst / "c.py").read_text() == "changed"
            assert (dst / "nested" / "d.py").read_text() == "d"

    def test_move(self, root):
        self._require("supports_move")
        # We write a temp file to move so we don't destroy a.txt for other tests
        src = root / "src_move.txt"
        src.write_text("data")
        dst = root / "dst_move.txt"
        src.move(dst)
        assert not src.exists()
        assert dst.read_text() == "data"

    def test_move_existing_target_raises_without_overwrite(self, root):
        self._require("supports_move")
        src = root / "src_move_existing.txt"
        src.write_text("data")
        dst = root / "b.py"
        with pytest.raises(FileExistsError):
            src.move(dst)
        assert src.read_text() == "data"
        assert dst.read_text() == "b"
        src.move(dst, overwrite=True)
        assert not src.exists()
        assert dst.read_text() == "data"

    def test_move_directory(self, root):
        self._require("supports_move")
        self._require("supports_mkdir")
        self._support(root, Create)
        src = root / "sub"
        dst = root / "moved_sub"
        src.move(dst)
        assert not src.exists()
        assert (dst / "c.py").read_text() == "c"
        assert (dst / "nested" / "d.py").read_text() == "d"

    def test_rename(self, root):
        self._require("supports_rename")
        self._support(root, Move)
        src = root / "src_rename.txt"
        src.write_text("data")
        dst = root / "dst_rename.txt"
        result = src.rename(dst)
        assert result.name == "dst_rename.txt"
        assert not src.exists()
        assert dst.read_text() == "data"
        with pytest.raises(FileNotFoundError):
            src.rename(root / "never.txt")

    def test_rename_onto_a_non_empty_directory_raises_and_keeps_both(self, root):
        self._require("supports_rename")
        self._support(root, Move)
        self._require("enforces_directory_hierarchy")
        src = root / "src_rename_onto_dir.txt"
        src.write_text("data")
        with pytest.raises(OSError):
            src.rename(root / "sub")
        assert src.read_text() == "data"
        assert (root / "sub" / "c.py").read_text() == "c"
        with pytest.raises(OSError):
            (root / "empty_dir").rename(root / "sub")
        assert (root / "empty_dir").is_dir()
        assert (root / "sub" / "nested" / "d.py").read_text() == "d"

    def test_touch(self, root):
        f = root / "touch_new.txt"
        f.touch()
        assert f.is_file()
        assert f.read_bytes() == b""
        (root / "a.txt").touch()
        assert (root / "a.txt").read_text() == "a"

    def test_utime_sets_the_time_of_an_existing_file_and_touch_moves_it(self, root):
        self._require("supports_utime")
        self._support(root, Utime)
        f = root / "utime_f.txt"
        f.write_text("kept")
        old = 1_000_000_000
        f._utime(mtime=old)
        assert int(f.stat().st_mtime) == old
        f._utime(mtime=old + 600.5)
        assert int(f.stat().st_mtime) == old + 600
        f._utime(atime=old + 5)
        assert int(f.stat().st_mtime) == old + 600
        f.touch()
        assert f.stat().st_mtime > old + 1_000
        assert f.read_text() == "kept"
        with pytest.raises(FileNotFoundError):
            (root / "utime_absent.txt")._utime(mtime=old)

    def test_touch_exist_ok_false_raises_without_truncating(self, root):
        f = root / "touch_f.txt"
        f.write_text("keep")
        with pytest.raises(FileExistsError):
            f.touch(exist_ok=False)
        assert f.read_text() == "keep"

    def test_mkdir_parents(self, root):
        self._require("supports_mkdir")
        self._support(root, Create)
        d = root / "parent_a" / "parent_b" / "parent_c"
        d.mkdir(parents=True)
        assert d.is_dir()
        with pytest.raises(FileExistsError):
            d.mkdir(parents=True, exist_ok=False)
        d.mkdir(parents=True, exist_ok=True)

    def test_listing_reflects_writes(self, root):
        self._require("supports_listing")
        (root / "empty_dir" / "new.txt").write_text("n")
        if self.supports_mkdir:
            (root / "empty_dir" / "new_sub").mkdir()
        expected = {"new.txt", "new_sub"} if self.supports_mkdir else {"new.txt"}
        assert {p.name for p in (root / "empty_dir").iterdir()} == expected
        if not self.supports_delete:
            return
        (root / "empty_dir" / "new.txt").unlink()
        if self.supports_mkdir:
            (root / "empty_dir" / "new_sub").rmdir()
        assert list((root / "empty_dir").iterdir()) == []
