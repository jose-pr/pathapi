"""The exceptions the package defines: one base, each also a builtin a caller
already catches."""

import pytest

import pathapi
from pathapi import NonRelativePatternError, PathError, glob


def test_the_root_exports_the_base_and_the_pattern_error():
    assert pathapi.PathError is PathError
    assert pathapi.NonRelativePatternError is NonRelativePatternError
    assert {"PathError", "NonRelativePatternError"} <= set(pathapi.__all__)


def test_the_base_is_an_exception_that_is_neither_an_oserror_nor_a_value_error():
    assert issubclass(PathError, Exception)
    assert not issubclass(PathError, (OSError, ValueError, NotImplementedError))


def test_a_pattern_error_is_a_path_error_and_the_builtins_pathlib_and_callers_catch():
    assert issubclass(NonRelativePatternError, PathError)
    assert issubclass(NonRelativePatternError, NotImplementedError)
    assert issubclass(NonRelativePatternError, ValueError)


@pytest.mark.parametrize("caught", [PathError, NotImplementedError, ValueError])
def test_an_absolute_glob_pattern_is_caught_as_each_of_its_bases(caught):
    with pytest.raises(caught):
        glob.parse_pattern("/etc/*.conf")


def test_the_pattern_error_has_one_home_outside_the_glob_module():
    assert NonRelativePatternError.__module__ == "pathapi.exceptions"
    assert "NonRelativePatternError" not in glob.__all__
