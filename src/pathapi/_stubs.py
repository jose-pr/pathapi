"""Placeholders for the methods a subclass has to supply."""

from __future__ import annotations

import functools as _functools
import typing as _ty

__all__ = ["derived_from", "notimplemented", "primitive"]

#: Set on every function that is only the default of a primitive;
#: `protocols.supports()` reads it.
STUB_ATTR = "__pathapi_stub__"


def primitive(method):
    """Mark `method` as the default of a primitive: it raises
    `NotImplementedError`, and a class that does not replace it does not
    implement the primitive."""
    setattr(method, STUB_ATTR, True)
    return method


def notimplemented(method):
    """Mark a method as the stub of a primitive. Used bare (`@notimplemented`)
    or with the reason a backend gives (`@notimplemented("why")`); calling the
    stub raises `NotImplementedError` with the reason, or with the method's
    name."""
    if isinstance(method, str):
        reason = method
        return lambda function: _stub(function, reason)
    return _stub(method, None)


def _stub(method, reason):
    @_functools.wraps(method)
    def _notimplemented(*args, **kwargs):
        raise NotImplementedError(
            reason or f"Method not implemented: {method.__name__}"
        )

    setattr(_notimplemented, STUB_ATTR, True)
    return _notimplemented


#: Set by `derived_from` on a default that only wraps other primitives.
DERIVED_ATTR = "__pathapi_derived_from__"


def derived_from(*primitives: str):
    """Mark a method as built on other primitives of the class, such as an
    `iterdir()` that lists through `_listdir()`: `protocols.supports()` counts
    it as implemented only when one of those primitives is."""

    def mark(function):
        setattr(function, DERIVED_ATTR, primitives)
        return function

    return mark


def derivation(function: object) -> "tuple[str, ...]":
    """The primitives `function` is built on (see `derived_from`), or `()`."""
    return getattr(function, DERIVED_ATTR, ())


def is_stub(function: object) -> bool:
    """Whether `function` is the default of a primitive (see `primitive`)."""
    return getattr(function, STUB_ATTR, False) is True


def _unimplemented(name: str) -> _ty.NoReturn:
    """Raise the error `notimplemented` raises for `name`. A protocol stub
    returns this call rather than `...` or `raise NotImplementedError`, which
    mypy and pyright read as "abstract": a class that subclasses the protocol
    explicitly then cannot be instantiated."""
    raise NotImplementedError(f"Method not implemented: {name}")
