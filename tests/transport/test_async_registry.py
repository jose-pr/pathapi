"""The async HTTP transports a distribution can provide: listed without
importing them, loaded by name from the `pathapi.http_async_transports`
entry-point group, and chosen by `PATHAPI_HTTP_ASYNC_TRANSPORT`; and the
client's use of the one it builds."""

from __future__ import annotations

import asyncio

import pytest

import pathapi.http as http
from pathapi.http import (
    AsyncHTTPClient,
    HTTPTransport,
    ThreadedTransport,
    async_transport_names,
    get_async_transport,
)
from pathapi.http import _async_transports
from transport_distribution import install, run
from transport_fakes import FakeTransport, reply

GROUP = "pathapi.http_async_transports"
GROUP_HINT = f"an async transport registers in the entry-point group '{GROUP}'"
VARIABLE = "PATHAPI_HTTP_ASYNC_TRANSPORT"


class _Entry:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        return self._target


def declare(monkeypatch, **entries):
    monkeypatch.setattr(
        _async_transports,
        "_declared_async",
        lambda: {name: _Entry(name, target) for name, target in entries.items()},
    )


@pytest.fixture
def nothing_declared(monkeypatch):
    declare(monkeypatch)


def test_the_names_the_module_states_are_the_ones_the_code_reads():
    assert http.ASYNC_TRANSPORT_GROUP == GROUP
    assert http.ASYNC_TRANSPORT_ENV == VARIABLE


def test_only_the_built_in_transport_is_listed_when_nothing_is_declared(
    nothing_declared,
):
    assert async_transport_names() == ("builtin",)


def test_an_entry_of_the_group_is_listed_and_loaded_by_name(monkeypatch):
    class Other:
        pass

    declare(monkeypatch, other=Other)
    assert async_transport_names() == ("builtin", "other")
    assert get_async_transport("other") is Other


def test_the_built_in_async_transport_is_the_threaded_one_and_no_entry_replaces_it(
    monkeypatch,
):
    declare(monkeypatch, builtin=object)
    assert get_async_transport("builtin") is ThreadedTransport


def test_an_entry_that_is_not_a_class_is_a_type_error(monkeypatch):
    declare(monkeypatch, odd="not callable")
    with pytest.raises(TypeError, match="'odd'"):
        get_async_transport("odd")


def test_a_name_nothing_declares_suggests_a_name_to_look_for(nothing_declared):
    with pytest.raises(ValueError) as raised:
        get_async_transport("httpx")
    assert str(raised.value) == (
        "'httpx' is not an async HTTP transport (expected one of 'builtin'); "
        f"{GROUP_HINT}: look for a distribution named 'pathapi-httpx' "
        "(a name to look for, not a known package)"
    )


@pytest.mark.parametrize("name", ["httpx", "", "Builtin", None, 3])
def test_an_unknown_name_is_both_a_value_error_and_an_import_error(
    nothing_declared, name
):
    with pytest.raises(ValueError):
        get_async_transport(name)
    with pytest.raises(ImportError):
        get_async_transport(name)


@pytest.mark.parametrize("name", ["", "  ", "a b", "x/y", None, 3])
def test_a_name_that_cannot_be_part_of_a_distribution_name_suggests_none(
    nothing_declared, name
):
    with pytest.raises(ValueError) as raised:
        get_async_transport(name)
    assert "look for" not in str(raised.value)


def test_the_variable_naming_an_unknown_transport_is_reported_when_it_is_needed(
    nothing_declared, monkeypatch
):
    monkeypatch.setenv(VARIABLE, "httpx")
    client = AsyncHTTPClient()  # nothing is read yet
    with pytest.raises(ValueError) as raised:
        client.transport
    assert str(raised.value).startswith(f"{VARIABLE}='httpx' is not an async HTTP")
    assert isinstance(raised.value, ImportError)


@pytest.mark.parametrize("value", ["", "Builtin", " builtin"])
def test_a_value_that_is_not_a_choice_is_a_value_error_naming_the_choices(
    nothing_declared, monkeypatch, value
):
    monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(ValueError) as raised:
        AsyncHTTPClient().transport
    assert "'builtin'" in str(raised.value) and GROUP in str(raised.value)


def test_the_synchronous_variable_does_not_choose_the_async_transport(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    monkeypatch.setenv("PATHAPI_HTTP_TRANSPORT", "nonsense")
    client = AsyncHTTPClient()
    assert type(client.transport) is ThreadedTransport


def test_the_default_transport_runs_the_synchronous_one_the_variable_names(
    monkeypatch,
):
    monkeypatch.delenv("PATHAPI_HTTP_TRANSPORT", raising=False)
    monkeypatch.delenv(VARIABLE, raising=False)

    async def scenario():
        async with AsyncHTTPClient() as client:
            assert type(client.transport) is ThreadedTransport
            assert type(client.transport.transport) is HTTPTransport

    asyncio.run(scenario())

    monkeypatch.setenv("PATHAPI_HTTP_TRANSPORT", "nonsense")

    async def bad():
        async with AsyncHTTPClient() as client:
            with pytest.raises(ValueError, match="PATHAPI_HTTP_TRANSPORT"):
                await client.request("GET", "http://127.0.0.1:1/x")

    asyncio.run(bad())


# --- the client's closer -------------------------------------------------------


def test_a_client_closes_the_transport_it_built_and_not_one_it_was_given(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    closed = []

    class Built(ThreadedTransport):
        async def aclose(self):
            closed.append(True)

    declare(monkeypatch)
    monkeypatch.setattr(_async_transports, "get_async_transport", lambda name: Built)

    async def scenario():
        sync = FakeTransport(default=reply(200))
        given = ThreadedTransport(sync)
        await AsyncHTTPClient(given).aclose()
        assert sync.closed == 0

        built = AsyncHTTPClient()
        assert built.transport is built.transport
        await built.aclose()
        await built.aclose()
        assert closed == [True]

    asyncio.run(scenario())


def test_a_transport_with_a_plain_send_is_a_type_error():
    class Sync:
        def send(self, *args, **kwargs):
            raise AssertionError("not reached")

    with pytest.raises(TypeError):
        AsyncHTTPClient(Sync())
    with pytest.raises(TypeError):
        AsyncHTTPClient(object())


def test_the_representation_of_a_client_shows_no_header():
    client = AsyncHTTPClient(headers={"Authorization": "Bearer SECRET"})
    assert "SECRET" not in repr(client)


# --- a distribution that is installed ---------------------------------------

PROVIDER = """
    class Fake:
        def __init__(self, **arguments):
            self.arguments = arguments

        async def send(self, method, url, *, headers, body=None, timeout, stream=False):
            raise NotImplementedError

        async def aclose(self):
            pass

    class Other(Fake):
        pass
"""


@pytest.fixture
def provider(tmp_path):
    return install(
        tmp_path,
        "fake-async-transports",
        {
            GROUP: {
                "httpx": "fake_async_transports:Fake",
                "other": "fake_async_transports:Other",
                "builtin": "fake_async_transports:Other",
            }
        },
        {"fake_async_transports": PROVIDER},
    )


def test_an_installed_distribution_is_listed_without_being_imported(provider):
    found = run(
        """
        import json, sys
        from pathapi.http import async_transport_names, transport_names
        print(json.dumps([
            list(async_transport_names()),
            list(transport_names()),
            "fake_async_transports" in sys.modules,
        ]))
        """,
        provider,
    )
    assert found == [["builtin", "httpx", "other"], ["builtin"], False]


def test_an_installed_distribution_provides_the_transport_by_name(provider):
    found = run(
        """
        import json
        from pathapi.http import get_async_transport
        import fake_async_transports
        print(json.dumps([
            get_async_transport("httpx") is fake_async_transports.Fake,
            get_async_transport("builtin").__name__,
        ]))
        """,
        provider,
    )
    assert found == [True, "ThreadedTransport"]


def test_the_variable_names_an_installed_transport(provider):
    found = run(
        """
        import asyncio, json
        from pathapi.http import AsyncHTTPClient

        async def main():
            async with AsyncHTTPClient() as client:
                return type(client.transport).__name__

        print(json.dumps(asyncio.run(main())))
        """,
        provider,
        env={VARIABLE: "other"},
    )
    assert found == "Other"
