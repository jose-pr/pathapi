"""A request that changes state is not redirected the way a browser does: a
307 or 308 to the same scheme, host and port is sent once more with the same
method and body, and every other redirect is an error, whichever transport
sends the requests."""

import errno
import io

import pytest

from pathlib_next.http import HTTPClient
from transport_wire import Wire, response

pytestmark = pytest.mark.usefixtures("http_transport_kind")

NOT_RESENT = [301, 302, 303]
RESENT = [307, 308]


def redirect(location, status):
    return response(status, {"Location": location}, reason="Moved")


def refused(excinfo, status, location):
    error = excinfo.value
    assert error.errno == errno.EIO
    assert str(status) in str(error) and location in str(error)
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("method", ["PUT", "DELETE", "POST", "PATCH"])
@pytest.mark.parametrize("status", NOT_RESENT)
def test_a_redirect_that_is_not_307_or_308_is_an_error_and_nothing_is_resent(
    method, status
):
    with Wire(lambda r: redirect("/store/f.bin", status)) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request(method, wire.url + "/f.bin", data=b"NEW")
    refused(excinfo, status, f"{wire.url}/store/f.bin")
    assert [(r.method, r.target) for r in wire.requests] == [(method, "/f.bin")]


@pytest.mark.parametrize("status", [300, 304])
def test_any_other_3xx_answer_to_a_write_is_an_error(status):
    with Wire(lambda r: response(status, reason="Other")) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/f.bin", data=b"NEW")
    assert excinfo.value.errno == errno.EIO and str(status) in str(excinfo.value)
    assert len(wire.requests) == 1


@pytest.mark.parametrize("status", RESENT)
@pytest.mark.parametrize("style", ["relative", "absolute"])
def test_307_and_308_send_the_same_method_and_the_whole_body_again(status, style):
    def script(request):
        if request.target != "/moved/f.bin":
            return response(201, reason="Created")
        location = "/store/f.bin" if style == "relative" else f"{wire.url}/store/f.bin"
        return redirect(location, status)

    with Wire(script) as wire:
        with HTTPClient() as client:
            reply = client.request(
                "PUT", wire.url + "/moved/f.bin", data=b"NEW-PAYLOAD"
            )
            assert reply.status == 201
    assert [(r.method, r.target, r.body) for r in wire.requests] == [
        ("PUT", "/moved/f.bin", b"NEW-PAYLOAD"),
        ("PUT", "/store/f.bin", b"NEW-PAYLOAD"),
    ]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_propfind_is_sent_again_after_any_of_these_redirects(status):
    def script(request):
        if request.target == "/dir":
            return redirect("/dir/", status)
        return response(207, {}, b"<multistatus/>", reason="Multi-Status")

    with Wire(script) as wire:
        with HTTPClient() as client:
            reply = client.request(
                "PROPFIND",
                wire.url + "/dir",
                data=b"<propfind/>",
                headers={"Depth": "0"},
            )
            assert reply.status == 207
    assert [(r.method, r.target, r.body) for r in wire.requests] == [
        ("PROPFIND", "/dir", b"<propfind/>"),
        ("PROPFIND", "/dir/", b"<propfind/>"),
    ]


def test_a_second_redirect_is_an_error():
    def script(request):
        if request.target == "/chain":
            return redirect("/again", 307)
        return redirect("/store/f.bin", 307)

    with Wire(script) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/chain", data=b"NEW")
    refused(excinfo, 307, f"{wire.url}/store/f.bin")
    assert [(r.method, r.target) for r in wire.requests] == [
        ("PUT", "/chain"),
        ("PUT", "/again"),
    ]


@pytest.mark.parametrize(
    "target",
    [
        "http://localhost:{port}/store/f.bin",
        "http://127.0.0.1:1/store/f.bin",
        "https://127.0.0.1:{port}/store/f.bin",
        "//localhost:{port}/store/f.bin",
    ],
    ids=["other-host", "other-port", "other-scheme", "other-host-no-scheme"],
)
def test_307_to_another_scheme_host_or_port_is_not_followed(target):
    with Wire(lambda r: response()) as wire:
        target = target.format(port=wire.port)
        wire.script = lambda r: redirect(target, 307)
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request(
                    "PUT",
                    wire.url + "/away/f.bin",
                    data=b"NEW",
                    headers={"Authorization": "Bearer SECRET"},
                )
    expected = "http:" + target if target.startswith("//") else target
    refused(excinfo, 307, expected)
    assert [r.target for r in wire.requests] == ["/away/f.bin"]


def test_a_307_without_a_location_is_an_error():
    with Wire(lambda r: response(307, reason="Temporary Redirect")) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/nowhere", data=b"NEW")
    assert excinfo.value.errno == errno.EIO and "307" in str(excinfo.value)
    assert len(wire.requests) == 1


def test_a_refused_redirect_never_shows_the_userinfo_of_its_location():
    with Wire() as wire:
        target = f"http://user:hunter2@127.0.0.1:{wire.port}/store/f.bin"
        wire.script = lambda r: redirect(target, 301)
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/leak", data=b"NEW")
    refused(excinfo, 301, f"127.0.0.1:{wire.port}/store/f.bin")
    assert "hunter2" not in str(excinfo.value) and "user:" not in str(excinfo.value)


def test_a_followed_redirect_ignores_the_userinfo_of_its_location():
    with Wire() as wire:
        target = f"http://user:hunter2@127.0.0.1:{wire.port}/store/f.bin"

        def script(request):
            if request.target == "/leak":
                return redirect(target, 307)
            return response(201, reason="Created")

        wire.script = script
        with HTTPClient() as client:
            client.request("PUT", wire.url + "/leak", data=b"NEW")
    assert [r.target for r in wire.requests] == ["/leak", "/store/f.bin"]
    assert all(r.header("Authorization") is None for r in wire.requests)


def test_a_resent_request_keeps_the_credentials_of_its_origin():
    def script(request):
        if request.target == "/r307":
            return redirect("/store/f.bin", 307)
        return response(201, reason="Created")

    with Wire(script) as wire:
        with HTTPClient() as client:
            client.request(
                "PUT", wire.url + "/r307", data=b"NEW", auth=("user", "hunter2")
            )
    first, second = wire.requests
    assert first.header("Authorization") == second.header("Authorization")
    assert first.header("Authorization").startswith("Basic ")


def test_a_refused_redirect_names_the_url_without_a_query_or_fragment():
    with Wire(lambda r: redirect("/store/f.bin?token=hunter2", 302)) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/f.bin?key=hunter3", data=b"NEW")
    assert "hunter2" not in str(excinfo.value) and "hunter3" not in str(excinfo.value)
    assert f"{wire.url}/f.bin" in str(excinfo.value)


def test_allow_redirects_does_not_make_a_write_follow_a_redirect():
    with Wire(lambda r: redirect("/store/f.bin", 302)) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError):
                client.request(
                    "PUT", wire.url + "/f.bin", data=b"NEW", allow_redirects=True
                )
    assert [r.method for r in wire.requests] == ["PUT"]


def test_a_body_that_cannot_be_sent_twice_is_not_resent_after_a_307():
    with Wire(lambda r: redirect("/store/f.bin", 307)) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                client.request("PUT", wire.url + "/r307", data=io.BytesIO(b"abc"))
    assert excinfo.value.errno == errno.EIO
    assert [r.target for r in wire.requests] == ["/r307"]


def test_a_read_still_follows_a_redirect():
    def script(request):
        if request.target == "/r302":
            return redirect("/store/f.bin", 302)
        return response(body=b"CONTENT")

    with Wire(script) as wire:
        with HTTPClient() as client:
            assert client.request("GET", wire.url + "/r302").read() == b"CONTENT"
    assert [r.method for r in wire.requests] == ["GET", "GET"]


def test_a_write_answered_by_a_redirect_that_is_not_followed_is_never_returned():
    # A 3xx the client does not follow must not come back as an answer that
    # looks like a finished write.
    with Wire(lambda r: response(302, {"Location": "/x"}, reason="Found")) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError):
                client.request("DELETE", wire.url + "/f.bin")
