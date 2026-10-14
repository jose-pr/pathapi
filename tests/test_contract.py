"""Filesystem contract shared by every Path implementation.

This is the reusable "one test class, many backends" pattern the project
wants third-party implementers (custom Path subclasses like MemPath) to be
able to run against their own implementation -- see
`docs/guides/extending.md`. The archive and FTP paths run it in
`test_archive_path.py` and `test_ftp_path.py`.
"""

import pytest

from capabilities import stores_question_marks

import pathlib_next
from pathlib_next.mempath import MemPath
from pathlib_next.testing import PathContract, populate_fixture_tree


class TestLocalContract(PathContract):
    supports_question_mark_names = stores_question_marks()

    @pytest.fixture
    def root(self, fixture_tree):
        return pathlib_next.LocalPath(fixture_tree)


class TestMemContract(PathContract):
    # MemPath has no rename(); move() falls back to copy + delete.
    supports_rename = False
    supports_question_mark_names = True

    @pytest.fixture
    def root(self):
        return populate_fixture_tree(MemPath("/"))
