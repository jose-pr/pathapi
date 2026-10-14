"""`protocols.supports()` and `capabilities()`: which protocols a class
implements, decided by whether each primitive is more than its default stub."""

from __future__ import annotations

import pathlib
import types

import pytest

from pathlib_next import LocalPath, MemPath, Path, Pathname
from pathlib_next import _stubs
from pathlib_next.ftp import FTPPath
from pathlib_next.archive import TarPath, ZipPath
from pathlib_next.protocols import (
    BinaryOpen,
    Chmod,
    Copy,
    Create,
    Hardlink,
    Identity,
    Listing,
    Move,
    NativeChecksum,
    Owner,
    Remove,
    Resolve,
    Stat,
    Symlink,
    Utime,
    capabilities,
    supports,
)

PROTOCOLS = {BinaryOpen, Chmod, NativeChecksum, Stat}


def test_the_default_of_each_primitive_is_marked_a_stub():
    for function in (
        Stat.stat,
        Chmod.chmod,
        Owner._chown,
        Owner._user_name,
        Owner._group_name,
        Utime._utime,
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
    assert not supports(MemPath, Owner)
    assert not supports(MemPath, NativeChecksum)
    assert not supports(MemPath, Stat, Chmod)


def test_a_bare_path_supports_only_the_protocols_that_have_no_primitive():
    class Bare(Path):
        __slots__ = ()

    assert capabilities(Bare) == {Identity, Copy}


def test_chmod_asks_for_chmod_only_though_it_inherits_ownership_and_times():
    assert supports(FTPPath, Chmod) and not supports(FTPPath, Owner)
    assert supports(MemPath, Utime) and not supports(MemPath, Chmod)
    assert Owner in Chmod.__mro__ and Utime in Chmod.__mro__


def test_local_path_supports_what_pathlib_provides():
    # `open`, `stat`, `iterdir`, `mkdir` and `rename` are pathlib's;
    # `_open` and `_mkdir` are not defined at all.
    assert supports(LocalPath, Stat, Chmod, BinaryOpen, Create, Listing, Remove)
    assert supports(LocalPath, Symlink, Move, Identity, Copy, Utime)
    assert supports(LocalPath, Hardlink, Resolve)
    # `_chown` is defined on every platform, so this holds on Windows too,
    # where pathlib's own `owner()` raises.
    assert supports(LocalPath, Owner)
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
    everywhere = {Stat, BinaryOpen, Create, Listing, Remove, Identity, Copy, Resolve}
    assert capabilities(LocalPath) == everywhere | {
        Chmod,
        Owner,
        Utime,
        Symlink,
        Hardlink,
        Move,
    }
    assert capabilities(MemPath) == everywhere | {Utime}
    assert capabilities(FTPPath) == everywhere | {Chmod, Utime, Move}
    # An archive reads its links and cannot make one.
    assert capabilities(ZipPath) == everywhere | {Move, Symlink}
    assert capabilities(TarPath) == everywhere | {Move, Symlink}


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


class _Owned(MemPath):
    """A memory path whose stat reports an owner and group by id."""

    __slots__ = ()

    def stat(self, *, follow_symlinks=True):
        return types.SimpleNamespace(
            st_mode=0o100644, st_size=0, st_mtime=0, st_uid=5, st_gid=6
        )

    def _user_name(self, uid):
        return f"user{uid}"

    def _group_name(self, gid):
        return f"group{gid}"


def test_owner_and_group_map_the_ids_of_stat_through_the_primitives():
    here = _Owned("/f")
    assert here.owner() == "user5"
    assert here.group() == "group6"


def test_owner_passes_follow_symlinks_to_stat():
    class Recording(_Owned):
        __slots__ = ()
        seen = []

        def stat(self, *, follow_symlinks=True):
            self.seen.append(follow_symlinks)
            return super().stat(follow_symlinks=follow_symlinks)

    here = Recording("/f")
    here.owner(follow_symlinks=False)
    here.group()
    assert Recording.seen == [False, True]


def test_owner_without_ids_in_stat_or_without_names_is_not_implemented():
    class Anonymous(MemPath):
        __slots__ = ()

        def stat(self, *, follow_symlinks=True):
            return types.SimpleNamespace(st_mode=0o100644, st_size=0, st_mtime=0)

    with pytest.raises(NotImplementedError, match="st_uid"):
        Anonymous("/f").owner()
    with pytest.raises(NotImplementedError, match="st_gid"):
        Anonymous("/f").group()

    class NoNames(_Owned):
        __slots__ = ()
        _user_name = Owner._user_name
        _group_name = Owner._group_name

    with pytest.raises(NotImplementedError, match="_user_name"):
        NoNames("/f").owner()
    with pytest.raises(NotImplementedError, match="_group_name"):
        NoNames("/f").group()


def test_local_path_owner_and_group_are_pathlibs(tmp_path):
    for name in ("owner", "group"):
        ours = getattr(LocalPath(tmp_path), name)
        theirs = getattr(pathlib.Path(tmp_path), name)
        try:
            expected = theirs()
        except NotImplementedError:
            with pytest.raises(NotImplementedError):
                ours()
        else:
            assert ours() == expected


def test_a_memory_path_cannot_name_its_owner_or_group():
    here = MemPath("/f")
    here.write_text("x")
    with pytest.raises(NotImplementedError):
        here.owner()
    with pytest.raises(NotImplementedError):
        here.group()
