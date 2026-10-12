from __future__ import annotations

import contextlib as _contextlib
import typing as _ty

from .. import UriPath


def _scandir(self):
    with self._delegated():
        yield from self._core_path._scandir()


def _listdir(self):
    for name, _stat in self._scandir():
        yield name


def _forward(name: str):
    """A method that calls the core path's method of the same name, with the
    errors it raises naming this path."""

    def method(self, *args, **kwargs):
        with self._delegated():
            return getattr(self._core_path, name)(*args, **kwargs)

    method.__name__ = name
    return method


#: Every hook a scheme may delegate, by name; `_DELEGATED` of the class says
#: which of them it does, so a hook the core path lacks stays unimplemented.
_FORWARDERS: "dict[str, object]" = {
    name: _forward(name)
    for name in (
        "_open",
        "_mkdir",
        "unlink",
        "rmdir",
        "chmod",
        "_chown",
        "_utime",
        "_symlink_to",
        "readlink",
        "_write_batch",
        "_same_filesystem",
        "_node_key",
    )
}
_FORWARDERS["_scandir"] = _scandir
_FORWARDERS["_listdir"] = _listdir


class DelegatingPath(UriPath):
    """A `UriPath` whose I/O is done by a core path that needs no URI.

    A scheme names the hooks it hands over in `_DELEGATED` and builds the core
    path for its URI in `_make_core()`; everything about URIs (parsing,
    joining, backends, the text of the path) stays here. The core path is made
    once per instance and used for every call, and an `OSError` it raises names
    this path, not the core one."""

    __slots__ = ("_core",)
    _DELEGATED: "_ty.Sequence[str]" = ()
    #: Whether an `OSError` of the core path is rewritten to name this path; a
    #: core path that is itself the user's file (`LocalPath`) keeps its own.
    _RELABEL = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in cls.__dict__.get("_DELEGATED", ()):
            if name not in cls.__dict__:
                setattr(cls, name, _FORWARDERS[name])

    def _make_core(self):
        """The core path this URI names."""
        raise NotImplementedError

    @property
    def _core_path(self):
        core = self._core
        if core is None:
            core = self._core = self._make_core()
        return core

    @_contextlib.contextmanager
    def _delegated(self, *extra: "tuple[str, str]"):
        """Runs a call of the core path; an `OSError` that names the core path
        (or a pair of `extra` `(core text, this text)`) names this one."""
        if not self._RELABEL:
            yield
            return
        try:
            yield
        except OSError as error:
            core = str(self._core_path)
            names = {**({core: str(self)} if core else {}), **dict(extra)}
            _relabel(error, names)
            raise

    def stat(self, *, follow_symlinks=True):
        hint = self._pop_stat_hint()
        if hint is not None and not (follow_symlinks and hint.is_symlink()):
            return hint
        with self._delegated():
            return self._core_path.stat(follow_symlinks=follow_symlinks)


def _relabel(error: OSError, names: "dict[str, str]") -> None:
    for attribute in ("filename", "filename2"):
        value = getattr(error, attribute)
        if value in names:
            setattr(error, attribute, names[value])
    if any(isinstance(arg, str) and arg in names for arg in error.args):
        error.args = tuple(
            names[arg] if isinstance(arg, str) and arg in names else arg
            for arg in error.args
        )
