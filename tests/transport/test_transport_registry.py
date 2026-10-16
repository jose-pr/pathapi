"""The HTTP transports a distribution can provide: listed without importing
them, loaded by name from the `pathapi.http_transports` entry-point group,
and chosen by `PATHAPI_HTTP_TRANSPORT`."""

from __future__ import annotations

import pytest

from pathapi.http import HTTPClient, HTTPTransport, _transports, get_transport
from pathapi.http import transport_names
from transport_distribution import install, run

GROUP_HINT = "a transport registers in the entry-point group 'pathapi.http_transports'"


def requests_message(subject):
    return (
        f"{subject} is not an HTTP transport (expected one of 'builtin'); "
        f"{GROUP_HINT}: look for a distribution named 'pathapi-requests' "
        "(a name to look for, not a known package)"
    )


@pytest.fixture
def nothing_declared(monkeypatch):
    """No installed distribution declares a transport."""
    monkeypatch.setattr(_transports, "_declared", lambda: {})


class _Entry:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        return self._target


def declare(monkeypatch, **entries):
    monkeypatch.setattr(
        _transports,
        "_declared",
        lambda: {name: _Entry(name, target) for name, target in entries.items()},
    )


def test_only_the_built_in_transport_is_listed_when_nothing_is_declared(
    nothing_declared,
):
    assert transport_names() == ("builtin",)


def test_an_entry_of_the_group_is_listed(monkeypatch):
    declare(monkeypatch, zeta=object)
    assert transport_names() == ("builtin", "zeta")


def test_the_group_is_the_one_distributions_declare():
    assert _transports.TRANSPORT_GROUP == "pathapi.http_transports"


def test_the_built_in_transport_needs_no_entry_point(nothing_declared):
    assert get_transport("builtin") is HTTPTransport


def test_an_entry_point_cannot_replace_the_built_in_transport(monkeypatch):
    class Other:
        pass

    declare(monkeypatch, builtin=Other)
    assert get_transport("builtin") is HTTPTransport


def test_a_declared_transport_is_loaded_by_name(monkeypatch):
    class Other:
        pass

    declare(monkeypatch, other=Other)
    assert get_transport("other") is Other


def test_an_entry_that_is_not_a_class_is_a_type_error(monkeypatch):
    declare(monkeypatch, odd="not callable")
    with pytest.raises(TypeError, match="'odd'"):
        get_transport("odd")


def test_a_name_nothing_declares_suggests_a_name_to_look_for(nothing_declared):
    with pytest.raises(ValueError) as raised:
        get_transport("requests")
    assert str(raised.value) == requests_message("'requests'")


def test_the_suggestion_is_derived_from_the_name_in_lower_case(nothing_declared):
    with pytest.raises(ValueError) as raised:
        get_transport("Fancy_Name")
    assert "look for a distribution named 'pathapi-fancy_name'" in str(raised.value)


@pytest.mark.parametrize("name", ["", "Requests", "BUILTIN", "urllib3", None, 3])
def test_a_name_that_is_not_a_transport_is_a_value_error_naming_the_choices(
    nothing_declared, name
):
    with pytest.raises(ValueError) as raised:
        get_transport(name)
    assert "expected one of 'builtin')" in str(raised.value)
    assert GROUP_HINT in str(raised.value)


@pytest.mark.parametrize("name", ["", "  ", "a b", "x/y", "-x", None, 3])
def test_a_name_that_cannot_be_part_of_a_distribution_name_suggests_none(
    nothing_declared, name
):
    with pytest.raises(ValueError) as raised:
        get_transport(name)
    assert "look for" not in str(raised.value)


def test_the_requests_transport_asked_for_by_the_environment_suggests_a_name(
    nothing_declared, monkeypatch
):
    monkeypatch.setenv("PATHAPI_HTTP_TRANSPORT", "requests")
    with HTTPClient() as client:
        with pytest.raises(ValueError) as raised:
            client.transport
    assert str(raised.value) == requests_message("PATHAPI_HTTP_TRANSPORT='requests'")


# --- a distribution that is installed ---------------------------------------

PROVIDER = """
    class Fake:
        def __init__(self, session=None, **arguments):
            self.session = session
            self.arguments = arguments
            self.closed = 0

        def send(self, method, url, *, headers, body=None, timeout, stream=False):
            raise NotImplementedError

        def close(self):
            self.closed += 1

    class Other(Fake):
        pass

    class NotBuiltin(Fake):
        pass
"""


@pytest.fixture
def provider(tmp_path):
    return install(
        tmp_path,
        "fake-transports",
        {
            "pathapi.http_transports": {
                "requests": "fake_transports:Fake",
                "other": "fake_transports:Other",
                "builtin": "fake_transports:NotBuiltin",
            }
        },
        {"fake_transports": PROVIDER},
    )


def test_an_installed_distribution_is_listed_without_being_imported(provider):
    found = run(
        """
        import json, sys
        from pathapi.http import transport_names
        print(json.dumps([list(transport_names()), "fake_transports" in sys.modules]))
        """,
        provider,
    )
    assert found == [["builtin", "other", "requests"], False]


def test_an_installed_distribution_provides_the_transport_by_name(provider):
    found = run(
        """
        import json
        from pathapi.http import get_transport
        import fake_transports
        print(json.dumps([
            get_transport("requests") is fake_transports.Fake,
            get_transport("other") is fake_transports.Other,
            get_transport("builtin").__name__,
        ]))
        """,
        provider,
    )
    assert found == [True, True, "HTTPTransport"]


@pytest.mark.parametrize("name, cls", [("requests", "Fake"), ("other", "Other")])
def test_the_variable_names_an_installed_transport(provider, name, cls):
    found = run(
        """
        import json
        from pathapi.http import HTTPClient
        with HTTPClient() as client:
            transport = client.transport
            print(json.dumps([type(transport).__name__, transport.closed]))
        """,
        provider,
        env={"PATHAPI_HTTP_TRANSPORT": name},
    )
    assert found == [cls, 0]


def test_a_value_the_variable_cannot_resolve_names_the_choices(provider):
    found = run(
        """
        import json
        from pathapi.http import HTTPClient
        try:
            HTTPClient().transport
        except ValueError as error:
            print(json.dumps(str(error)))
        """,
        provider,
        env={"PATHAPI_HTTP_TRANSPORT": "Other"},
    )
    assert found == (
        "PATHAPI_HTTP_TRANSPORT='Other' is not an HTTP transport "
        "(expected one of 'builtin', 'other', 'requests'); "
        f"{GROUP_HINT}: look for a distribution named 'pathapi-other' "
        "(a name to look for, not a known package)"
    )
