"""`Path` is the class that has every protocol: its operations are defined
in the protocols, and `Copy` is resolved before `BinaryOpen` for `copy`."""

from __future__ import annotations

import types

import pytest

from pathlib_next import LocalPath, MemPath, Path
from pathlib_next.protocols import (
    BinaryOpen,
    Chmod,
    Chown,
    Copy,
    Create,
    Hardlink,
    Identity,
    Listing,
    Move,
    Owned,
    Readlink,
    Remove,
    Resolve,
    Stat,
    Symlink,
    Utime,
)

OWNERS = {
    "info": Stat,
    "owner": Owned,
    "group": Owned,
    "chown": Chown,
    "_chown": Chown,
    "_user_name": Owned,
    "_group_name": Owned,
    "_utime": Utime,
    "chmod": Chmod,
    "lchmod": Chmod,
    "is_junction": Stat,
    "is_mount": Stat,
    "is_dir_binding": Stat,
    "iterdir": Listing,
    "_scandir": Listing,
    "glob": Listing,
    "rglob": Listing,
    "walk": Listing,
    "__iter__": Listing,
    "_mkdir": Create,
    "mkdir": Create,
    "touch": Create,
    "unlink": Remove,
    "rmdir": Remove,
    "rm": Remove,
    "readlink": Readlink,
    "_symlink_to": Symlink,
    "_hardlink_to": Hardlink,
    "hardlink_to": Hardlink,
    "_cwd": Resolve,
    "absolute": Resolve,
    "resolve": Resolve,
    "_symlink_target": Symlink,
    "symlink_to": Symlink,
    "_same_filesystem": Identity,
    "_node_key": Identity,
    "samefile": Identity,
    "copy": Copy,
    "copy_into": Copy,
    "_copy_from": Copy,
    "_copy_symlink": Copy,
    "_into": Copy,
    "_coerce_target": Copy,
    "_write_batch": Copy,
    "move": Move,
    "move_into": Move,
    "rename": Move,
    "replace": Move,
    "_rename_compatible": Move,
}


@pytest.mark.parametrize("name", sorted(OWNERS))
def test_each_operation_is_defined_by_its_protocol(name):
    owner = OWNERS[name]
    assert name in vars(owner)
    assert name not in vars(Path)
    assert getattr(Path, name) is getattr(owner, name) or isinstance(
        getattr(Path, name), property
    )


def test_path_defines_only_what_the_protocols_cannot():
    own = {
        name
        for name, value in vars(Path).items()
        if isinstance(value, (types.FunctionType, property, staticmethod, classmethod))
    }
    # `typing.Protocol` installs `__subclasshook__` on each class.
    own.discard("__subclasshook__")
    assert own == {"__new__", "__init_subclass__", "is_hidden"}


def test_copy_resolves_before_binary_open_in_every_path_class():
    for cls in (Path, MemPath, LocalPath):
        mro = cls.__mro__
        assert mro.index(Copy) < mro.index(BinaryOpen)
    assert Path.copy is Copy.copy
    assert Path.copy is not BinaryOpen.copy


def test_the_bases_of_the_protocols_are_the_ones_their_bodies_call():
    bases = {
        Owned: {Stat},
        Chown: set(),
        Chmod: {Chown, Utime},
        Create: {Stat, Chmod, Utime},
        Readlink: {Stat},
        Listing: {Stat},
        Remove: {Listing},
        Symlink: {Remove},
        Hardlink: {Stat},
        Resolve: {Readlink},
        Identity: {Stat},
        Copy: {Identity, Create, Symlink, Readlink, BinaryOpen},
        Move: {Copy, Remove},
    }
    for protocol, expected in bases.items():
        declared = {base for base in protocol.__bases__ if base.__name__ != "Protocol"}
        assert declared == expected, protocol.__name__


def test_path_mro_lists_the_protocols_in_the_order_of_its_class_statement():
    names = [cls.__name__ for cls in Path.__mro__]
    assert names[:17] == [
        "Path",
        "Pathname",
        "Move",
        "Copy",
        "Identity",
        "Create",
        "Symlink",
        "Remove",
        "Listing",
        "Resolve",
        "Readlink",
        "Hardlink",
        "Owned",
        "Stat",
        "Chmod",
        "Chown",
        "Utime",
    ]
