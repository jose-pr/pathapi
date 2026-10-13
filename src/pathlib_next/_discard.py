"""What `BinaryOpen.open()` does when the `with` block of a write stream raised.

A stream that commits on close (an upload, an archive rewrite) has a
`discard()` method. When the block ended in an exception, `discard()` is called
before the stream is closed, so the close commits nothing; when it ended
normally, or the stream is closed with `close()`, nothing changes.
"""

from __future__ import annotations

import copyreg as _copyreg
import functools as _functools
import io as _io
import typing as _ty


class DiscardOnExit:
    """Mixin for a stream with `discard()`: leaving a `with` block through an
    exception (any `BaseException`) calls `discard()` before the stream is
    closed."""

    __slots__ = ()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None:
                self.discard()
        except BaseException:
            super().__exit__(exc_type, exc_value, traceback)
            raise
        return super().__exit__(exc_type, exc_value, traceback)


def _new_instance(cls, *args):
    return cls.__new__(cls, *args)


def _new_instance_ex(cls, args, kwargs):
    return cls.__new__(cls, *args, **kwargs)


class _Guarded(DiscardOnExit):
    """The base `guard()` puts in front of a stream's own class. A pickle or a
    copy is made as for the stream's own class, so a stream that pickled before
    still does, and what it makes is a stream of that class."""

    __slots__ = ()
    _original: type

    def __reduce_ex__(self, protocol):
        guarded = type(self)
        self.__class__ = guarded._original
        try:
            reduced = self.__reduce_ex__(protocol)
        finally:
            self.__class__ = guarded
        if not isinstance(reduced, tuple):
            return reduced
        function, *rest = reduced
        # The pickler checks a `__newobj__` call against the class of the
        # object it saves, which is this one; a function of its own name
        # builds the same object without that check.
        if function is _copyreg.__newobj__:
            function = _new_instance
        elif function is _copyreg.__newobj_ex__:
            function = _new_instance_ex
        return (function, *rest)


@_functools.lru_cache(maxsize=64)
def _guarded_class(cls: type) -> "type | None":
    try:
        return type(cls)(
            cls.__name__,
            (_Guarded, cls),
            {
                "__slots__": (),
                "__module__": cls.__module__,
                "__qualname__": cls.__qualname__,
                "__doc__": cls.__doc__,
                "_original": cls,
            },
        )
    except Exception:
        return None


def guard(stream: _ty.Any) -> _ty.Any:
    """`stream` itself, made to call its own `discard()` when a `with` block
    over it raises. A stream with no `discard()`, or one that already does so
    in its `__exit__`, is returned untouched. The stream keeps its identity and
    stays an instance of its own class: its class is replaced by a subclass
    that adds the `__exit__`. A stream whose class cannot be subclassed that
    way (a type written in C) is left as it is and must discard in its own
    `__exit__`."""
    if isinstance(stream, DiscardOnExit) or not callable(
        getattr(stream, "discard", None)
    ):
        return stream
    guarded = _guarded_class(type(stream))
    if guarded is not None:
        try:
            stream.__class__ = guarded
        except (AttributeError, TypeError):
            pass
    return stream


class DiscardingTextIO(_io.TextIOWrapper):
    """A `TextIOWrapper` over a stream with `discard()`. Leaving a `with` block
    through an exception drops the text the wrapper still holds, calls
    `discard`, then closes: a stock wrapper would write that text to the stream
    and let the close commit it."""

    _dropping = False
    _discard: "_ty.Callable[[], object] | None" = None

    def __init__(self, stream, discard, *args, **kwargs):
        super().__init__(stream, *args, **kwargs)
        self._discard = discard

    def flush(self):
        if not self._dropping:
            super().flush()

    def close(self):
        if self.closed:
            return
        try:
            self.flush()
        except BaseException:
            # The stream has not been given all the text: it must not commit
            # what it has.
            self._dropping = True
            try:
                self._discard()
            finally:
                super().close()
            raise
        super().close()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return super().__exit__(exc_type, exc_value, traceback)
        self._dropping = True
        try:
            self._discard()
        finally:
            super().__exit__(exc_type, exc_value, traceback)


def abandon(stream: _ty.Any) -> None:
    """Close a stream whose `open()` failed after the backend gave it out,
    having it discard what it holds first so the failed call commits nothing."""
    try:
        discard = getattr(stream, "discard", None)
        if callable(discard):
            discard()
    finally:
        stream.close()
