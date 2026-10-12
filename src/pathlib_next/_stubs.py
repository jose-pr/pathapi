"""Placeholders for the methods a subclass has to supply."""

from __future__ import annotations

import functools as _functools
import typing as _ty

__all__ = ["notimplemented"]


def notimplemented(method):
    @_functools.wraps(method)
    def _notimplemented(*args, **kwargs):
        raise NotImplementedError(f"Method not implemented: {method.__name__}")

    return _notimplemented


def _unimplemented(name: str) -> _ty.NoReturn:
    """Raise the error `notimplemented` raises for `name`. A protocol stub
    returns this call rather than `...` or `raise NotImplementedError`, which
    mypy and pyright read as "abstract": a class that subclasses the protocol
    explicitly then cannot be instantiated."""
    raise NotImplementedError(f"Method not implemented: {name}")
