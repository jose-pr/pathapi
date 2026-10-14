"""What a backend with fewer primitives than `MemPath` needs from the generic
operations: a stub that says why, contract switches that follow `supports()`,
a copy that does not remove what cannot be removed, and a move that refuses
before it copies what it could not then remove."""

from __future__ import annotations

import pytest

from pathlib_next import MemPath
from pathlib_next.protocols import Create, Remove, supports
from pathlib_next.providers import notimplemented
from pathlib_next.testing import PathContract, ReadPathContract, populate_fixture_tree


class NoRemove(MemPath):
    """A memory path that can write and cannot delete."""

    __slots__ = ()
    unlink = Remove.unlink
    rmdir = Remove.rmdir


# --- a stub that gives its reason ----------------------------------------


def test_a_reasoned_stub_raises_its_reason_and_reads_as_absent():
    class NoDirectories(MemPath):
        __slots__ = ()

        @notimplemented("this transport has no directories")
        def _mkdir(self, mode):
            raise AssertionError("unreachable")

    with pytest.raises(NotImplementedError) as error:
        NoDirectories("/d").mkdir()
    assert str(error.value) == "this transport has no directories"
    assert not supports(NoDirectories, Create)


def test_a_bare_stub_still_names_the_method():
    class Bare(MemPath):
        __slots__ = ()

        @notimplemented
        def _mkdir(self, mode): ...

    with pytest.raises(NotImplementedError, match="_mkdir"):
        Bare("/d").mkdir()
    assert not supports(Bare, Create)


# --- contract switches that follow supports() ------------------------------


def _skips(contract, name, root):
    with pytest.raises(pytest.skip.Exception) as skipped:
        getattr(contract(), name)(root)
    return str(skipped.value)


def test_a_class_without_remove_skips_the_removal_tests_with_no_switch_set():
    contract = type("Contract", (PathContract,), {})
    root = populate_fixture_tree(NoRemove("/"))
    for name in ("test_unlink", "test_rmdir_requires_empty", "test_rm_recursive"):
        assert "NoRemove does not support Remove" in _skips(contract, name, root)


def test_a_switch_set_by_the_subclass_overrides_the_answer():
    contract = type("Contract", (PathContract,), {"supports_delete": True})
    root = populate_fixture_tree(NoRemove("/"))
    with pytest.raises(NotImplementedError):
        contract().test_unlink(root)


def test_a_class_with_every_protocol_skips_nothing_for_them():
    root = populate_fixture_tree(MemPath("/"))
    contract = ReadPathContract()
    contract.test_iterdir_lists_children(root)


# --- copy onto an existing file where nothing can be removed -----------------


def test_copy_over_an_existing_file_of_a_class_without_remove_replaces_it():
    backend = MemPath("/").backend
    source = MemPath("/src", backend=backend)
    source.write_text("new content")
    target = NoRemove("/dst", backend=backend)
    target.write_text("old")
    source.copy(target, overwrite=True)
    assert target.read_text() == "new content"


# --- move from a source that cannot be removed ---------------------------------


def test_move_from_a_class_without_remove_raises_before_the_target_exists():
    backend = MemPath("/").backend
    source = NoRemove("/src", backend=backend)
    source.write_text("kept")
    target = NoRemove("/dst", backend=backend)
    with pytest.raises(NotImplementedError, match="cannot be removed"):
        source.move(target)
    assert not target.exists()
    assert source.read_text() == "kept"


def test_move_from_a_class_without_remove_still_reports_a_missing_source():
    with pytest.raises(FileNotFoundError):
        NoRemove("/missing").move(NoRemove("/dst"))


# --- a wrapper that is implemented only when what it wraps is -----------------


def test_a_derived_method_counts_only_through_its_primitive():
    from pathlib_next import Path
    from pathlib_next.protocols import Listing
    from pathlib_next.providers import derived_from

    class Base(MemPath):
        __slots__ = ()

        @notimplemented
        def _listdir(self): ...

        @derived_from("_listdir")
        def iterdir(self):
            return iter(())

    class Lists(Base):
        __slots__ = ()

        def _listdir(self):
            return iter(())

    assert not supports(Base, Listing)
    assert supports(Lists, Listing)
    assert Path in Base.__mro__


def test_a_default_that_raises_its_own_error_reads_as_absent():
    from pathlib_next.protocols import Listing
    from pathlib_next.providers import primitive

    class Single(MemPath):
        __slots__ = ()

        @primitive
        def iterdir(self):
            raise NotADirectoryError(self)

    assert not supports(Single, Listing)
    with pytest.raises(NotADirectoryError):
        Single("/x").iterdir()
