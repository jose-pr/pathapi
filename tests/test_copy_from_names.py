"""`_copy_from()` joins only listed names that are one safe component."""

import io
import pathlib
import sys

import pytest

from pathapi.mempath import MemPath, MemPathBackend


class _Info:
    def __init__(self, directory):
        self._directory = directory

    def is_dir(self, *, follow_symlinks=True):
        return self._directory

    def is_symlink(self):
        return False


class _Child:
    """A file a hostile listing names `name`."""

    info = _Info(False)

    def __init__(self, name):
        self.name = name

    def open(self, mode="rb"):
        return io.BytesIO(b"payload")


class _Listing:
    """A directory whose listing is `names`."""

    info = _Info(True)

    def __init__(self, *names):
        self._names = names

    def iterdir(self):
        return iter([_Child(name) for name in self._names])


def _target():
    backend = MemPathBackend()
    return backend, MemPath("/", "out", backend=backend)


@pytest.mark.parametrize("name", ["..", ".", "a/b", "", "a\x00b"])
def test_an_unsafe_child_name_is_refused_and_nothing_is_written_for_it(name):
    backend, target = _target()
    with pytest.raises(ValueError, match="unsafe child name"):
        target._copy_from(_Listing(name))
    assert backend == {"out": {}}


def test_a_safe_name_before_an_unsafe_one_is_copied_and_the_error_names_the_second():
    backend, target = _target()
    with pytest.raises(ValueError, match=r"'\.\.'"):
        target._copy_from(_Listing("ok.txt", ".."))
    assert bytes(backend["out"]["ok.txt"]) == b"payload"
    assert set(backend["out"]) == {"ok.txt"}


def test_a_safe_name_is_copied():
    backend, target = _target()
    target._copy_from(_Listing("ok.txt"))
    assert bytes(backend["out"]["ok.txt"]) == b"payload"


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Path.copy() is 3.14")
def test_a_stdlib_copy_onto_a_path_of_the_library_refuses_a_hostile_listing(tmp_path):
    class Hostile(type(pathlib.Path())):
        def iterdir(self):
            yield _Child("..")

    backend, target = _target()
    with pytest.raises(ValueError, match="unsafe child name"):
        Hostile(tmp_path).copy(target)
    assert backend == {"out": {}}
