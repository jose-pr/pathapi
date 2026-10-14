import pathapi
import os
import posixpath
import pytest

pathnames = ["./test/path"]


@pytest.mark.parametrize("pathname", pathnames)
def test_local_as_posix(pathname: str):
    local = pathapi.LocalPath(pathname)
    assert local.as_posix() == posixpath.normpath(pathname)


@pytest.mark.parametrize("pathname", pathnames)
def test_local_str(pathname: str):
    local = pathapi.LocalPath(pathname)
    assert str(local) == os.path.normpath(pathname)


@pytest.mark.parametrize("pathname", pathnames)
def test_local_fspath(pathname: str):
    local = pathapi.LocalPath(pathname)
    assert local.__fspath__() == os.fspath(os.path.normpath(pathname))
