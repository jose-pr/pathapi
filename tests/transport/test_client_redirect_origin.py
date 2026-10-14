"""`Authorization` belongs to the origin a request was made for. A request
asked again after a redirect (`HTTPClient.redirect()`) leaves it off when the
target is another scheme, host or port and keeps it within the origin,
whichever transport sends."""

import errno

import pytest

from pathapi.http import HTTPClient, HTTPTransport
from transport_wire import Wire, make_authority, response

pytestmark = pytest.mark.usefixtures("http_transport_kind")

TOKEN = "Bearer SECRET-TOKEN"


def sees_no_credential(wire):
    return [r.header("Authorization") for r in wire.requests] == [None] * len(
        wire.requests
    )


def first_answer(client, method, url, **kwargs):
    """The redirect that `url` answers, unfollowed."""
    answer = client.request(method, url, allow_redirects=False, **kwargs)
    assert answer.status in (301, 302)
    answer.close()
    return answer


@pytest.fixture
def origins():
    """`home` redirects every HEAD and GET to `away`; both answer a file of
    five bytes."""
    with Wire(
        lambda r: response(200, {"Content-Type": "text/plain"}, b"hello")
    ) as away:

        def home_script(request):
            return response(302, {"Location": away.url + "/elsewhere"}, reason="Found")

        with Wire(home_script) as home:
            yield home, away


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("how", ["header", "auth"])
def test_a_request_asked_again_at_another_origin_does_not_carry_authorization(
    origins, method, how
):
    home, away = origins
    headers = {"X-Api-Key": "K"}
    auth = None
    if how == "header":
        headers["Authorization"] = TOKEN
    else:
        auth = ("user", "s3cr3t")
    with HTTPClient() as client:
        url = home.url + "/f"
        moved = first_answer(client, method, url, headers=headers, auth=auth)
        again = client.redirect(method, url, moved, headers=headers, auth=auth)
        assert again.status == 200
        again.close()
    # The credential reached the origin it was given for, and not the other.
    assert home.requests[0].header("Authorization") is not None
    assert away.requests and sees_no_credential(away)
    # Only Authorization is left off.
    assert away.requests[0].header("X-Api-Key") == "K"
    assert away.requests[0].target == "/elsewhere"


def test_a_request_asked_again_within_the_origin_keeps_authorization():
    def script(request):
        if request.target == "/d":
            return response(301, {"Location": "/d/"}, reason="Moved")
        return response(200, {"Content-Type": "text/html"}, b"<html></html>")

    with Wire(script) as wire:
        with HTTPClient(headers={"Authorization": TOKEN}) as client:
            url = wire.url + "/d"
            moved = first_answer(client, "HEAD", url)
            client.redirect("HEAD", url, moved).close()
    assert [r.target for r in wire.requests] == ["/d", "/d/"]
    assert [r.header("Authorization") for r in wire.requests] == [TOKEN, TOKEN]


def test_a_second_redirect_back_to_the_origin_does_not_bring_it_back():
    with Wire() as wire:
        with Wire(lambda r: response(302, {"Location": wire.url + "/home"})) as away:

            def script(request):
                if request.target == "/start":
                    return response(302, {"Location": away.url + "/away"})
                return response(200, {}, b"ok")

            wire.script = script
            with HTTPClient(headers={"Authorization": TOKEN}) as client:
                url = wire.url + "/start"
                moved = first_answer(client, "GET", url)
                client.redirect("GET", url, moved).read()
    assert [r.header("Authorization") for r in wire.requests] == [TOKEN, None]
    assert sees_no_credential(away)


def test_a_redirect_from_https_to_plain_http_does_not_carry_authorization(tmp_path):
    authority = make_authority(tmp_path, "127.0.0.1", "localhost")
    with Wire(lambda r: response(200, {}, b"plain")) as plain:
        secure_script = lambda r: response(  # noqa: E731
            302, {"Location": plain.url + "/downgraded"}, reason="Found"
        )
        with Wire(secure_script, tls=authority.server_context()) as secure:
            transport = HTTPTransport(ssl_context=authority.client_context())
            client = HTTPClient(transport, headers={"Authorization": TOKEN})
            try:
                assert client.request("GET", secure.url + "/f").read() == b"plain"
            finally:
                transport.close()
    assert secure.requests[0].header("Authorization") == TOKEN
    assert plain.requests and sees_no_credential(plain)


def test_a_re_request_with_no_usable_location_is_a_refused_redirect():
    with Wire(lambda r: response(302, {"Location": "ftp://127.0.0.1/x"})) as wire:
        with HTTPClient() as client:
            url = wire.url + "/f"
            moved = first_answer(client, "GET", url)
            with pytest.raises(OSError) as excinfo:
                client.redirect("GET", url, moved)
    assert excinfo.value.errno == errno.EIO
    assert "HTTP 302" in str(excinfo.value) and "no usable Location" in str(
        excinfo.value
    )
    assert len(wire.requests) == 1
