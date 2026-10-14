"""`supports()` and `capabilities()`: which protocols a class implements."""

from __future__ import annotations

import importlib as _importlib
import typing as _ty

from .. import _stubs

_PACKAGE = __name__.partition(".")[0]


def _is_protocol(candidate: object) -> bool:
    """Whether `candidate` is a protocol of this package: a class that lists
    its primitives."""
    return (
        isinstance(candidate, type)
        and "__primitives__" in vars(candidate)
        and (candidate.__module__ or "").startswith(_PACKAGE + ".protocols")
    )


def _resolve(cls: type, name: str) -> object:
    """What `cls.name` resolves to along the MRO, unwrapped to the function
    for a static or class method; None when no class defines it."""
    for base in cls.__mro__:
        if name in vars(base):
            value = vars(base)[name]
            return getattr(value, "__func__", value)
    return None


def _defined_outside(value: object) -> bool:
    """Whether `value` comes from neither this library's protocols nor its
    `Path`: a class's own method, a mixin's or the standard library's."""
    module = getattr(value, "__module__", "") or ""
    return not (
        module.startswith(_PACKAGE + ".protocols")
        or module == _PACKAGE + ".path"
        or module.startswith(_PACKAGE + ".path.")
    )


def _has(cls: type, entry: "str | tuple[str, ...]") -> bool:
    """Whether `cls` implements one entry of `__primitives__`. An entry is a
    primitive name, or a tuple whose first name is the primitive and whose
    others are operations that replace it when something other than the
    protocol's own default defines them (`mkdir` for `_mkdir`, `open` for
    `_open`: `pathlib.Path` has the operation and not the primitive)."""
    names = (entry,) if isinstance(entry, str) else entry
    primitive = _resolve(cls, names[0])
    if primitive is not None and not _stubs.is_stub(primitive):
        return True
    for name in names[1:]:
        replacement = _resolve(cls, name)
        if replacement is not None and _defined_outside(replacement):
            return True
    return False


def _static(cls: type, protocol: type) -> bool:
    if not protocol.__primitives__ and protocol not in cls.__mro__:
        # Nothing to implement: the operations come with the protocol.
        return False
    return all(_has(cls, entry) for entry in protocol.__primitives__)


def supports(obj: object, *protocols: type) -> bool:
    """Whether `obj` (an instance or a class) implements every protocol
    named: each primitive of each resolves to something other than the
    protocol's default stub. A class whose answer depends on its connection
    defines `_supports(protocol) -> bool | None`, asked first for an instance;
    `None` falls through to the answer from the class.

    The answer is advisory, like `supported_checksums()`: a primitive may
    still raise `NotImplementedError` for an argument it cannot handle.
    `TypeError` for no protocol, or for something that is not a protocol of
    this package.
    """
    if not protocols:
        raise TypeError("supports() needs at least one protocol")
    for protocol in protocols:
        if not _is_protocol(protocol):
            raise TypeError(f"{protocol!r} is not a protocol of {_PACKAGE}")
    instance = None if isinstance(obj, type) else obj
    cls = obj if isinstance(obj, type) else type(obj)
    for protocol in protocols:
        if instance is not None:
            hook = getattr(cls, "_supports", None)
            if callable(hook):
                answer = hook(instance, protocol)
                if answer is not None:
                    if not answer:
                        return False
                    continue
        if not _static(cls, protocol):
            return False
    return True


def capabilities(obj: object) -> "_ty.FrozenSet[type]":
    """The protocols of this package that `obj` (an instance or a class)
    supports, in the sense of `supports()`."""
    module = _importlib.import_module(_PACKAGE + ".protocols")
    known = [
        value
        for value in (getattr(module, name) for name in module.__all__)
        if _is_protocol(value)
    ]
    return frozenset(protocol for protocol in known if supports(obj, protocol))
