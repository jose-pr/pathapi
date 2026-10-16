"""`AsyncPathContract` over a local tree, an in-memory tree and a loopback FTP
server: the thread-backed `AsyncPath` gives what the sync path gives."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import urllib.parse

import pytest


import pathapi
from pathapi.ftp import FTPBackend, FTPPath
from pathapi import AsyncPath
from pathapi.mempath import MemPath
from pathapi.testing import AsyncPathContract, populate_fixture_tree


class TestLocalAsyncContract(AsyncPathContract):
    @pytest.fixture
    def root(self, fixture_tree):
        return pathapi.LocalPath(fixture_tree)


class TestMemAsyncContract(AsyncPathContract):
    @pytest.fixture
    def root(self):
        return populate_fixture_tree(MemPath("/"))


class TaggedAsyncPath(AsyncPath):
    __slots__ = ()


class TestSubclassAsyncContract(AsyncPathContract):
    async_path_class = TaggedAsyncPath

    @pytest.fixture
    def root(self):
        return populate_fixture_tree(MemPath("/"))


def test_the_contract_fails_when_a_derived_path_is_not_the_class_under_test():
    class Plain(AsyncPath):
        __slots__ = ()

        @classmethod
        def _adopt(cls, path):
            return AsyncPath(path)

    class Contract(AsyncPathContract):
        async_path_class = Plain

    root = populate_fixture_tree(MemPath("/"))
    with pytest.raises(AssertionError):
        Contract().test_the_pure_members_are_plain_and_give_async_paths(root)


class TestFTPAsyncContract(AsyncPathContract):
    @pytest.fixture
    def root(self, ftp_server):
        parts = urllib.parse.urlsplit(ftp_server)
        backend = FTPBackend(
            parts.hostname,
            parts.port,
            user=parts.username,
            password=parts.password,
            timeout=10,
        )
        try:
            yield FTPPath("/", backend=backend)
        finally:
            backend.close()


def test_the_contract_fails_for_a_wrapper_that_answers_wrongly(monkeypatch):
    async def always_true(self, **kwargs):
        return True

    monkeypatch.setattr(AsyncPath, "exists", always_true)
    root = populate_fixture_tree(MemPath("/"))
    with pytest.raises(AssertionError):
        AsyncPathContract().test_exists_and_the_type_tests_answer_as_the_sync_path_does(
            root
        )


def test_importing_the_contracts_does_not_load_asyncio_until_it_is_named():
    source = str(pathlib.Path(pathapi.__file__).resolve().parent.parent)
    code = (
        "import sys, pathapi.testing; before = 'asyncio' in sys.modules; "
        "pathapi.testing.AsyncPathContract; "
        "print(before, 'asyncio' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=source),
    )
    assert result.stdout.split() == ["False", "True"], result.stderr
