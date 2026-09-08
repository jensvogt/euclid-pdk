"""The transport: what goes on the wire, and what happens when a connection turns out to be dead."""

from __future__ import annotations

import http.client

import pytest

from euclid.http import EuclidHttpClient, Response


def test_response_reads_its_body_three_ways():
    response = Response(200, "OK", {}, b'{"total": 2}')
    assert response.ok
    assert response.text == '{"total": 2}'
    assert response.json() == {"total": 2}

    assert not Response(404, "Not Found").ok
    # An empty body is an empty object, not a JSON parse error - some actions answer with nothing.
    assert Response(200, "OK").json() == {}


def test_post_sends_the_target_and_action_headers(gateway):
    gateway.answer("eam", "ping", {"pong": True})
    gateway.public.add(("eam", "ping"))

    with EuclidHttpClient(timeout=5) as client:
        response = client.post(gateway.base_url + "/", '{"a":1}', "eam", "ping",
                               {"Content-Type": "application/json"})

    assert response.status == 200
    assert response.json() == {"pong": True}
    assert gateway.last().headers["x-euclid-target"] == "eam"
    assert gateway.last().headers["x-euclid-action"] == "ping"
    assert gateway.last().body == b'{"a":1}'


def test_a_connection_is_reused_across_calls(gateway):
    """Keep-alive is the point of caching one connection per thread; without it every action pays
    for a new TCP - and, in production, TLS - handshake."""
    gateway.answer("eam", "ping", {"ok": True})
    gateway.public.add(("eam", "ping"))

    with EuclidHttpClient(timeout=5) as client:
        client.post(gateway.base_url + "/", "{}", "eam", "ping")
        first = dict(client._local.connections)
        client.post(gateway.base_url + "/", "{}", "eam", "ping")
        second = dict(client._local.connections)

    assert list(first.values()) == list(second.values())
    assert len(gateway.requests) == 2


def test_a_dead_cached_connection_is_retried_once(gateway):
    """A socket closed while it sat idle is not noticed until the next write, and shows up as a
    failure with no response at all. That is the case worth one more attempt."""
    gateway.answer("eam", "ping", {"ok": True})
    gateway.public.add(("eam", "ping"))

    client = EuclidHttpClient(timeout=5)
    url = gateway.base_url + "/"
    client.post(url, "{}", "eam", "ping")

    # Stand in for a connection the far end closed: it fails exactly the way a dead one does.
    (key,) = client._local.connections
    client._local.connections[key] = _DeadConnection()

    assert client.post(url, "{}", "eam", "ping").json() == {"ok": True}
    assert len(gateway.requests) == 2
    client.close()


def test_a_connection_that_stays_dead_raises(monkeypatch):
    """One retry, not a loop: a server that is genuinely gone is reported, not hammered."""
    client = EuclidHttpClient(timeout=5)
    attempts = []

    def always_dead(*args: object, **kwargs: object) -> _DeadConnection:
        attempts.append(1)
        return _DeadConnection()

    monkeypatch.setattr(client, "_connection", always_dead)

    with pytest.raises(http.client.RemoteDisconnected):
        client._send("POST", "http://127.0.0.1:9/", b"{}", {}, None)
    assert len(attempts) == 2
    client.close()


def test_a_missing_ca_certificate_is_not_an_error():
    """The default CA path points at where euclid installs its certificate, so on a machine that
    has no euclid it must quietly fall back to the system trust store."""
    client = EuclidHttpClient(ca_cert_path="/nonexistent/euclid_cert.crt")
    assert client.ca_cert_path == "/nonexistent/euclid_cert.crt"


def test_verification_can_be_turned_off_for_development_servers():
    import ssl

    client = EuclidHttpClient(verify=False)
    assert client._ssl_context.verify_mode == ssl.CERT_NONE
    assert client._ssl_context.check_hostname is False


class _DeadConnection:
    """A connection that fails the way one closed by the far end does."""

    timeout = 5

    def request(self, *args: object, **kwargs: object) -> None:
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    def getresponse(self) -> None:  # pragma: no cover - request() always raises first
        raise AssertionError("unreachable")

    def close(self) -> None:
        pass
