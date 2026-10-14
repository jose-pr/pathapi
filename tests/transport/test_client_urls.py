"""The helpers for a URL that carries credentials, and for a reply that
redirects."""

import pytest

from pathapi.http import is_redirect, split_userinfo
from transport_fakes import reply


@pytest.mark.parametrize(
    "url, plain, credentials",
    [
        ("http://alice:s3cr3t@h/x", "http://h/x", ("alice", "s3cr3t")),
        (
            "https://al%40ice:p%3Aw@h:8443/x?q=1#f",
            "https://h:8443/x?q=1#f",
            ("al@ice", "p:w"),
        ),
        ("http://alice@h/x", "http://h/x", ("alice", "")),
        ("http://:secret@h/x", "http://h/x", ("", "secret")),
        ("http://alice:caf%C3%A9@h/", "http://h/", ("alice", "café".encode())),
        ("http://alice:%FF%FE@h/", "http://h/", ("alice", b"\xff\xfe")),
        ("http://h/x@y", "http://h/x@y", None),
        ("http://h/x?mail=a@b", "http://h/x?mail=a@b", None),
        ("http://h/x", "http://h/x", None),
        ("/relative", "/relative", None),
        ("http://@h/x", "http://h/x", None),
    ],
)
def test_userinfo_is_split_out_of_a_url_as_the_octets_it_stands_for(
    url, plain, credentials
):
    found_url, found = split_userinfo(url)
    assert found_url == plain
    if credentials is None:
        assert found is None
        return
    user, password = credentials
    expected = (
        user.encode() if isinstance(user, str) and not user.isascii() else user,
        password,
    )
    assert found == expected


def test_the_url_left_after_the_split_carries_no_password():
    url, _ = split_userinfo("http://alice:s3cr3t@h/x")
    assert "s3cr3t" not in url and "alice" not in url


@pytest.mark.parametrize(
    "status, headers, expected",
    [
        (301, {"Location": "/x"}, True),
        (302, {"Location": "/x"}, True),
        (303, {"location": "/x"}, True),
        (307, {"Location": "/x"}, True),
        (308, {"Location": "/x"}, True),
        (302, {}, False),
        (300, {"Location": "/x"}, False),
        (304, {"Location": "/x"}, False),
        (200, {"Location": "/x"}, False),
    ],
)
def test_a_reply_redirects_when_its_status_says_so_and_it_names_a_location(
    status, headers, expected
):
    assert is_redirect(reply(status, headers=headers)) is expected
