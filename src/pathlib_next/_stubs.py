"""Placeholders for the methods a subclass has to supply."""

from __future__ import annotations

import functools as _functools
import typing as _ty

__all__ = ["notimplemented", "primitive"]

#: Set on every function that is only the default of a primitive;
#: `protocols.supports()` reads it.
STUB_ATTR = "__pathlib_next_stub__"


def primitive(method):
    """Mark `method` as the default of a primitive: it raises
    `NotImplementedError`, and a class that does not replace it does not
    implement the primitive."""
    setattr(method, STUB_ATTR, True)
    return method


def notimplemented(method):
    @_functools.wraps(method)
    def _notimplemented(*args, **kwargs):
        raise NotImplementedError(f"Method not implemented: {method.__name__}")

    setattr(_notimplemented, STUB_ATTR, True)
    return _notimplemented


def is_stub(function: object) -> bool:
    """Whether `function` is the default of a primitive (see `primitive`)."""
    return getattr(function, STUB_ATTR, False) is True


def _unimplemented(name: str) -> _ty.NoReturn:
    """Raise the error `notimplemented` raises for `name`. A protocol stub
    returns this call rather than `...` or `raise NotImplementedError`, which
    mypy and pyright read as "abstract": a class that subclasses the protocol
    explicitly then cannot be instantiated."""
    raise NotImplementedError(f"Method not implemented: {name}")
