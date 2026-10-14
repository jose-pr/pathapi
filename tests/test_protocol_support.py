"""`protocols.supports()` and `capabilities()`: which protocols a class
implements, decided by whether each primitive is more than its default stub."""

from __future__ import annotations

import pathlib

import pytest

from pathlib_next import LocalPath, MemPath, Path, Pathname
from pathlib_next import _stubs
from pathlib_next.ftp import FTPPath
from pathlib_next.archive import ZipPath
from pathlib_next.protocols import (
    BinaryOpen,
    Chmod,
    Copy,
    Create,
    Identity,
    Listing,
    Move,
    NativeChecksum,
    Remove,
    Stat,
    Symlink,
    capabilities,
    supports,
)

PROTOCOLS = {BinaryOpen, Chmod, NativeChecksum, Stat}


def test_the_default_of_each_primitive_is_marked_a_stub():
    for function in (
        Stat.stat,
        Chmod.chmod,
        Chmod._chown,
        Chmod._utime,
        BinaryOpen._open,
        NativeChecksum.checksum,
    ):
        assert _stubs.is_stub(function), function.__qualname__


def test_a_derived_operation_is_not_a_stub():
    for function in (Stat.exists, Stat.is_dir, BinaryOpen.read_bytes, Chmod.chown):
        assert not _stubs.is_stub(function), function.__qualname__


def test_notimplemented_and_primitive_mark_what_they_wrap():
    @_stubs.notimplemented
    def one(self): ...

    @_stubs.primitive
    def two(self):
        return _stubs._unimplemented("two")

    def three(self): ...

    assert _stubs.is_stub(one) and _stubs.is_stub(two)
    assert not _stubs.is_stub(three)
    with pytest.raises(NotImplementedError, match="one"):
        one(None)


def test_a_class_that_implements_the_primitives_supports_the_protocol():
    assert supports(MemPath, BinaryOpen, Stat)
    assert supports(MemPath("/a"), BinaryOpen, Stat)


def test_a_class_without_a_primitive_does_not_support_its_protocol():
    assert not supports(MemPath, Chmod)
    assert not supports(MemPath, NativeChecksum)
    assert not supports(MemPath, Stat, Chmod)


def test_a_bare_path_supports_only_the_protocols_that_have_no_primitive():
    class Bare(Path):
        __slots__ = ()

    assert capabilities(Bare) == {Identity, Copy}


def test_local_path_supports_what_pathlib_provides():
    # `open`, `stat`, `iterdir`, `mkdir` and `rename` are pathlib's;
    # `_open` and `_mkdir` are not defined at all.
    assert supports(LocalPath, Stat, Chmod, BinaryOpen, Create, Listing, Remove)
    assert supports(LocalPath, Symlink, Move, Identity, Copy)
    assert not supports(LocalPath, NativeChecksum)


def test_a_primitive_replaced_by_an_operation_of_another_library_counts():
    class Opens(Pathname, BinaryOpen):
        __slots__ = ()

        def open(
            self, mode="r", buffering=-1, encoding=None, errors=None, newline=None
        ):
            return None

    assert supports(Opens, BinaryOpen)
    assert not supports(Pathname, BinaryOpen)


def test_capabilities_of_the_providers_of_the_core():
    everywhere = {Stat, BinaryOpen, Create, Listing, Remove, Identity, Copy}
    assert capabilities(LocalPath) == everywhere | {Chmod, Symlink, Move}
    assert capabilities(MemPath) == everywhere
    assert capabilities(FTPPath) == everywhere | {Chmod, Move}
    assert capabilities(ZipPath) == everywhere | {Move}


def test_capabilities_of_an_instance_equal_those_of_its_class():
    assert capabilities(MemPath("/x")) == capabilities(MemPath)


def test_the_hook_decides_for_an_instance_and_none_falls_through():
    class Connected(MemPath):
        __slots__ = ()
        answer = None

        def _supports(self, protocol):
            if protocol is Stat:
                return False
            if protocol is Chmod:
                return True
            return self.answer

    here = Connected("/x")
    assert not supports(here, Stat)
    assert supports(here, Chmod)
    assert supports(here, BinaryOpen)
    assert not supports(here, BinaryOpen, Stat)
    assert supports(Connected, Stat, BinaryOpen)
    assert not supports(Connected, Symlink)
    assert Stat not in capabilities(here)
    assert Chmod in capabilities(here)


@pytest.mark.parametrize("bad", [int, pathlib.PurePath, object, "Stat", None])
def test_something_that_is_not_a_protocol_of_the_package_is_a_type_error(bad):
    with pytest.raises(TypeError):
        supports(MemPath, bad)


def test_no_protocol_is_a_type_error():
    with pytest.raises(TypeError):
        supports(MemPath)
