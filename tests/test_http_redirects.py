"""Redirects and credentials above a transport: the backend follows a `GET`,
`HEAD` or `OPTIONS` redirect itself and takes `Authorization` off when the
scheme, host or port changes, whichever transport sends the requests."""

import pytest

from http_fakes import FakeTransport, reply
from http_wire import Wire, response
from pathlib_next.uri.schemes.http import HttpBackend

AUTH = {"Authorization": "Bearer TOKEN-1"}


def redirect(location, status=302):
    return response(status, {"Location": location}, reason="Found")


def sent_authorization(request):
    return request.header("Authorization")


# --- across a real transport -------------------------------------------------


def test_authorization_goes_with_a_redirect_that_stays_on_the_origin(http_transport):
    def script(request):
        return (
            redirect("/final") if request.target == "/start" else response(body=b"ok")
        )

    with Wire(script) as wire:
        backend = HttpBackend(http_transport, headers=AUTH)
        reply_ = backend.request("GET", wire.url + "/start")
        assert reply_.read() == b"ok"
    assert [sent_authorization(r) for r in wire.requests] == ["Bearer TOKEN-1"] * 2


def test_authorization_is_dropped_by_a_redirect_to_another_port(http_transport):
    with Wire() as elsewhere:

        def script(request):
            return redirect(elsewhere.url + "/landing")

        with Wire(script) as wire:
            backend = HttpBackend(http_transport, headers={**AUTH, "X-Api-Key": "K"})
            backend.request("GET", wire.url + "/start").read()
    assert sent_authorization(wire.requests[0]) == "Bearer TOKEN-1"
    (landing,) = elsewhere.requests
    assert landing.target == "/landing"
    assert sent_authorization(landing) is None
    # Only Authorization is dropped.
    assert landing.header("X-Api-Key") == "K"


def test_authorization_from_url_userinfo_is_dropped_the_same_way(http_transport):
    with Wire() as elsewhere:
        with Wire(lambda r: redirect(elsewhere.url + "/landing")) as wire:
            backend = HttpBackend(http_transport)
            backend.request(
                "GET", f"http://alice:s3cr3t@127.0.0.1:{wire.port}/s"
            ).read()
    assert sent_authorization(wire.requests[0]).startswith("Basic ")
    assert sent_authorization(elsewhere.requests[0]) is None


def test_authorization_does_not_come_back_when_a_redirect_returns_to_the_origin(
    http_transport,
):
    with Wire() as wire:
        with Wire(lambda r: redirect(wire.url + "/back")) as away:

            def script(request):
                if request.target == "/start":
                    return redirect(away.url + "/away")
                return response(body=b"home")

            wire.script = script
            backend = HttpBackend(http_transport, headers=AUTH)
            backend.request("GET", wire.url + "/start").read()
    assert sent_authorization(wire.requests[0]) == "Bearer TOKEN-1"
    assert sent_authorization(wire.requests[1]) is None


def test_a_303_becomes_a_get_and_a_307_keeps_the_method(http_transport):
    def script(request):
        if request.target == "/see":
            return redirect("/there", 303)
        if request.target == "/again":
            return redirect("/there", 307)
        return response(body=b"there")

    with Wire(script) as wire:
        backend = HttpBackend(http_transport)
        backend.request("OPTIONS", wire.url + "/see").read()
        backend.request("OPTIONS", wire.url + "/again").read()
    assert [(r.method, r.target) for r in wire.requests] == [
        ("OPTIONS", "/see"),
        ("GET", "/there"),
        ("OPTIONS", "/again"),
        ("OPTIONS", "/there"),
    ]


def test_a_redirect_loop_stops_after_thirty_redirects(http_transport):
    with Wire(lambda r: redirect("/loop")) as wire:
        backend = HttpBackend(http_transport)
        with pytest.raises(OSError) as excinfo:
            backend.request("GET", wire.url + "/loop")
    assert len(wire.requests) == 31
    assert "30 redirects" in str(excinfo.value)


@pytest.mark.parametrize(
    "location",
    [
        "/a\x01b",
        "ftp://127.0.0.1/x",
        "file:///etc/hosts",
        "javascript:alert(1)",
    ],
)
def test_a_location_that_is_not_an_http_url_is_not_followed(http_transport, location):
    with Wire(lambda r: redirect(location)) as wire:
        backend = HttpBackend(http_transport)
        reply_ = backend.request("GET", wire.url + "/start")
        assert reply_.status == 302
        reply_.close()
    assert len(wire.requests) == 1


def test_userinfo_in_a_location_is_not_sent(http_transport):
    with Wire() as elsewhere:
        target = f"http://mallory:LEAKED@127.0.0.1:{elsewhere.port}/landing"
        with Wire(lambda r: redirect(target)) as wire:
            HttpBackend(http_transport).request("GET", wire.url + "/s").read()
    assert elsewhere.requests[0].target == "/landing"
    assert sent_authorization(elsewhere.requests[0]) is None


def test_allow_redirects_false_returns_the_redirect(http_transport):
    with Wire(lambda r: redirect("/there")) as wire:
        reply_ = HttpBackend(http_transport).request(
            "GET", wire.url + "/start", allow_redirects=False
        )
        assert (reply_.status, reply_.headers["Location"]) == (302, "/there")
        reply_.close()
    assert len(wire.requests) == 1


# --- the scheme is part of the origin ----------------------------------------


def fake_redirecting(location):
    def answer(method, url, **kwargs):
        if url == "http://h:443/x":
            return reply(302, headers={"Location": location}, url=url)
        return reply(200, b"final", url=url)

    return FakeTransport(handler=answer)


@pytest.mark.parametrize(
    "location, kept",
    [
        ("https://h/x", False),  # same host and port number, another scheme
        ("http://h:444/x", False),
        ("http://other/x", False),
        ("http://h:443/y", True),
        ("//h:443/y", True),
    ],
)
def test_authorization_follows_only_a_redirect_that_keeps_scheme_host_and_port(
    location, kept
):
    transport = fake_redirecting(location)
    HttpBackend(transport, headers=AUTH).request("GET", "http://h:443/x").read()
    assert len(transport.sent) == 2
    assert ("Authorization" in transport.sent[1].headers) is kept


def test_a_header_with_a_line_break_is_refused_before_any_transport_call():
    transport = FakeTransport(default=reply(200))
    backend = HttpBackend(transport, headers={"X-A": "1\r\nX-B: 2"})
    with pytest.raises(ValueError):
        backend.request("GET", "http://h/x")
    with pytest.raises(ValueError):
        HttpBackend(transport).request("GET", "http://h/x", headers={"X-A": "1\n2"})
    with pytest.raises(ValueError):
        HttpBackend(transport).request("GET", "http://h/x\r\nX-A: 1")
    assert transport.sent == []
