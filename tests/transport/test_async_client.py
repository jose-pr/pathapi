"""`AsyncHTTPClient` over an async transport against loopback servers:
redirects and the rule for `Authorization`, the limit on a reply read whole,
the deadline, and the choice of the default transport. Each test runs once per
async transport."""

import asyncio
import errno

import pytest

import pathapi.http as http
from pathapi.http import AsyncHTTPClient, AsyncHTTPResponse
from transport_fakes import make_async_transport
from transport_wire import Hang, Reply, Wire, response

AUTH = {"Authorization": "Bearer TOKEN-1"}


def redirect(location, status=302):
    return response(status, {"Location": location}, reason="Found")


def run(scenario):
    return asyncio.run(scenario())


# --- the transport the client sends with ------------------------------------------


def test_the_variable_chooses_the_transport_a_client_builds(async_http_transport_kind):
    async def scenario():
        async with AsyncHTTPClient() as client:
            expected = http.get_async_transport(async_http_transport_kind)
            assert type(client.transport) is expected

    run(scenario)


def test_a_client_sends_through_the_transport_it_builds(async_http_transport_kind):
    async def scenario():
        async with AsyncHTTPClient() as client:
            reply = await client.request("GET", wire.url + "/x")
            assert isinstance(reply, AsyncHTTPResponse)
            assert (reply.status, await reply.read()) == (200, b"ok")

    with Wire() as wire:
        run(scenario)
    assert wire.requests[0].header("User-Agent").startswith("pathapi/")


# --- across a real transport ------------------------------------------------------


def test_authorization_goes_with_a_redirect_that_stays_on_the_origin(
    async_http_transport_kind,
):
    def script(request):
        return (
            redirect("/final") if request.target == "/start" else response(body=b"ok")
        )

    async def scenario():
        async with AsyncHTTPClient(headers=AUTH) as client:
            reply = await client.request("GET", wire.url + "/start")
            assert await reply.read() == b"ok"

    with Wire(script) as wire:
        run(scenario)
    assert [r.header("Authorization") for r in wire.requests] == ["Bearer TOKEN-1"] * 2


def test_authorization_is_dropped_by_a_redirect_to_another_port(
    async_http_transport_kind,
):
    async def scenario():
        async with AsyncHTTPClient(headers={**AUTH, "X-Api-Key": "K"}) as client:
            await (await client.request("GET", wire.url + "/start")).read()

    with Wire() as elsewhere:
        with Wire(lambda r: redirect(elsewhere.url + "/landing")) as wire:
            run(scenario)
    assert wire.requests[0].header("Authorization") == "Bearer TOKEN-1"
    (landing,) = elsewhere.requests
    assert landing.target == "/landing"
    assert landing.header("Authorization") is None
    assert landing.header("X-Api-Key") == "K"


def test_a_303_becomes_a_get_and_a_307_keeps_the_method(async_http_transport_kind):
    def script(request):
        if request.target == "/see":
            return redirect("/there", 303)
        if request.target == "/again":
            return redirect("/there", 307)
        return response(body=b"there")

    async def scenario():
        async with AsyncHTTPClient() as client:
            await (await client.request("OPTIONS", wire.url + "/see")).read()
            await (await client.request("OPTIONS", wire.url + "/again")).read()

    with Wire(script) as wire:
        run(scenario)
    assert [(r.method, r.target) for r in wire.requests] == [
        ("OPTIONS", "/see"),
        ("GET", "/there"),
        ("OPTIONS", "/again"),
        ("OPTIONS", "/there"),
    ]


def test_a_redirect_loop_stops_after_thirty_redirects(async_http_transport_kind):
    async def scenario():
        async with AsyncHTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                await client.request("GET", wire.url + "/loop")
            assert "30 redirects" in str(excinfo.value)

    with Wire(lambda r: redirect("/loop")) as wire:
        run(scenario)
    assert len(wire.requests) == 31


@pytest.mark.parametrize(
    "location", ["/a\x01b", "ftp://127.0.0.1/x", "javascript:alert(1)"]
)
def test_a_location_that_is_not_an_http_url_is_a_refused_redirect(
    async_http_transport_kind, location
):
    async def scenario():
        async with AsyncHTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                await client.request("GET", wire.url + "/start")
            assert excinfo.value.errno == errno.EIO

    with Wire(lambda r: redirect(location)) as wire:
        run(scenario)
    assert len(wire.requests) == 1


def test_allow_redirects_false_returns_the_redirect_and_redirect_follows_it(
    async_http_transport_kind,
):
    def script(request):
        return (
            redirect("/final") if request.target == "/start" else response(body=b"ok")
        )

    async def scenario():
        async with AsyncHTTPClient() as client:
            url = wire.url + "/start"
            first = await client.request("GET", url, allow_redirects=False)
            assert (first.status, first.headers["Location"]) == (302, "/final")
            await first.aclose()
            final = await client.redirect("GET", url, first)
            assert await final.read() == b"ok"

    with Wire(script) as wire:
        run(scenario)
    assert [r.target for r in wire.requests] == ["/start", "/final"]


def test_a_write_redirected_somewhere_else_is_not_sent_there(
    async_http_transport_kind,
):
    async def scenario():
        async with AsyncHTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                await client.request("PUT", wire.url + "/f", data=b"x")
            assert excinfo.value.errno == errno.EIO

    with Wire() as elsewhere:
        with Wire(lambda r: redirect(elsewhere.url + "/f", 307)) as wire:
            run(scenario)
    assert elsewhere.requests == []


# --- the size of a reply and the deadline -----------------------------------------


def test_a_reply_over_the_limit_is_efbig_when_read_whole_and_fine_streamed(
    async_http_transport_kind, monkeypatch
):
    monkeypatch.setattr(http, "MAX_REPLY_BYTES", 1000)

    async def scenario():
        async with AsyncHTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                await client.request("GET", wire.url + "/big")
            assert excinfo.value.errno == errno.EFBIG
            streamed = await client.request("GET", wire.url + "/big", stream=True)
            assert len(await streamed.read()) == 5000
            await streamed.aclose()

    with Wire(lambda r: response(body=b"x" * 5000)) as wire:
        run(scenario)


def test_a_reply_within_the_limit_is_returned_read_and_closed(
    async_http_transport_kind,
):
    async def scenario():
        async with AsyncHTTPClient() as client:
            reply = await client.request("GET", wire.url + "/")
            await reply.aclose()
            assert await reply.read() == b"ok"

    with Wire() as wire:
        run(scenario)


def test_a_server_that_never_answers_is_cut_off_at_the_deadline(
    async_http_transport_kind,
):
    async def scenario():
        async with AsyncHTTPClient(timeout=(5, 30), deadline=0.4) as client:
            with pytest.raises(TimeoutError):
                await client.request("GET", wire.url + "/")

    with Wire(lambda r: Hang()) as wire:
        run(scenario)


def test_a_streamed_body_is_cut_off_at_the_deadline_between_reads(
    async_http_transport_kind,
):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\npartial")

    async def scenario():
        async with AsyncHTTPClient(timeout=(5, 5), deadline=0.3) as client:
            reply = await client.request("GET", wire.url + "/", stream=True)
            assert await reply.read(7) == b"partial"
            await asyncio.sleep(0.4)
            with pytest.raises(TimeoutError):
                await reply.read(1)
            await reply.aclose()

    with Wire(script) as wire:
        run(scenario)


def test_requests_of_one_client_run_together(async_http_transport_kind):
    import threading

    gate = threading.Barrier(3, timeout=10)

    def script(request):
        gate.wait()
        return response(body=request.target.encode())

    async def scenario():
        async with AsyncHTTPClient() as client:
            replies = await asyncio.gather(
                *(client.request("GET", f"{wire.url}/{n}") for n in range(3))
            )
            return sorted([await reply.read() for reply in replies])

    with Wire(script) as wire:
        assert run(scenario) == [b"/0", b"/1", b"/2"]


def test_a_client_given_a_transport_leaves_it_open(async_http_transport_kind):
    async def scenario():
        transport = make_async_transport(async_http_transport_kind)
        try:
            async with AsyncHTTPClient(transport) as client:
                await (await client.request("GET", wire.url + "/")).read()
            # Still usable: the client did not close what it was given.
            await (
                await AsyncHTTPClient(transport).request("GET", wire.url + "/")
            ).read()
        finally:
            await transport.aclose()

    with Wire() as wire:
        run(scenario)
    assert len(wire.requests) == 2
