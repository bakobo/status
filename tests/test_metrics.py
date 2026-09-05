"""The one module that cannot be exercised without a network, kept small enough to test anyway.

What is asserted here is the request built and the response accepted — not a rehearsal of urllib.
The transport seam takes a callable, so the default path (real HTTP) is still reached in the two
tests that monkeypatch `urlopen`, which is where the error translation lives.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error

import pytest

from bakobo_status import errors, metrics
from bakobo_status.errors import StatusError
from bakobo_status.metrics import Prometheus, _auth_header

AT = dt.datetime(2026, 9, 6, tzinfo=dt.timezone.utc)


def responder(payload, capture=None):
    def transport(url, headers):
        if capture is not None:
            capture.append((url, headers))
        return json.dumps(payload).encode()
    return transport


def test_grafana_reads_basic_auth_not_a_bearer_token():
    """The username is the numeric Prometheus id. A bearer token here yields a 401 that blames
    the token, which is how an evening gets spent on the wrong half."""
    header = _auth_header("12345", "glc_secret")
    assert header.startswith("Basic ")
    import base64
    assert base64.b64decode(header[6:]).decode() == "12345:glc_secret"


def test_an_instant_query_sends_the_expression_and_the_instant():
    seen = []
    client = Prometheus("https://prom.example/", "1", "t",
                        transport=responder({"status": "success", "data": {"result": [1]}}, seen))
    assert client.query("up", AT) == [1]

    url, headers = seen[0]
    assert url.startswith("https://prom.example/api/prom/api/v1/query?")
    assert "query=up" in url
    assert str(AT.timestamp()).split(".")[0] in url
    assert headers["Authorization"].startswith("Basic ")


def test_a_range_query_sends_start_end_and_step():
    seen = []
    client = Prometheus("https://prom.example", "1", "t",
                        transport=responder({"status": "success", "data": {"result": []}}, seen))
    client.query_range("up", AT - dt.timedelta(days=1), AT, 900)

    url, _ = seen[0]
    assert "/api/prom/api/v1/query_range?" in url
    assert "step=900" in url


def test_a_trailing_slash_in_the_base_url_does_not_double_up():
    seen = []
    Prometheus("https://prom.example/", "1", "t",
               transport=responder({"status": "success", "data": {}}, seen)).query("up", AT)
    assert "//api/prom" not in seen[0][0]


def test_an_empty_result_is_returned_rather_than_treated_as_an_error():
    """A check that did not exist yet legitimately has no series."""
    client = Prometheus("https://p", "1", "t",
                        transport=responder({"status": "success", "data": {}}))
    assert client.query("up", AT) == []
    assert client.query_range("up", AT, AT, 900) == []


def test_a_rejected_query_names_the_reason():
    client = Prometheus("https://p", "1", "t", transport=responder(
        {"status": "error", "errorType": "bad_data", "error": "parse error at char 3"}))
    with pytest.raises(StatusError) as caught:
        client.query("up{", AT)
    assert caught.value.code == errors.METRICS_REFUSED
    assert "parse error at char 3" in caught.value.detail
    assert "defect here, not a" in caught.value.detail


def test_a_rejected_query_with_no_reason_still_says_something_useful():
    client = Prometheus("https://p", "1", "t", transport=responder({"status": "error"}))
    with pytest.raises(StatusError) as caught:
        client.query("up", AT)
    assert "no reason given" in caught.value.detail


def test_a_non_json_response_suggests_the_likely_cause():
    """Pointing the base URL at the stack rather than the Prometheus instance returns HTML."""
    client = Prometheus("https://p", "1", "t", transport=lambda u, h: b"<html>hello</html>")
    with pytest.raises(StatusError) as caught:
        client.query("up", AT)
    assert caught.value.code == errors.METRICS_REFUSED
    assert "points at the stack" in caught.value.detail


# --- the real transport's error translation ---------------------------------------------------

def test_an_http_error_surfaces_the_body_and_blames_the_right_credential(monkeypatch):
    def raise_401(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"invalid credentials"))

    monkeypatch.setattr(metrics.urllib.request, "urlopen", raise_401)
    with pytest.raises(StatusError) as caught:
        Prometheus("https://p", "1", "t").query("up", AT)
    assert caught.value.code == errors.METRICS_REFUSED
    assert "invalid credentials" in caught.value.detail
    assert "instance id rather than the token" in caught.value.detail


def test_an_unreachable_endpoint_is_retryable_but_says_not_to_retry_quietly(monkeypatch):
    def raise_dns(request, timeout):
        raise urllib.error.URLError("Name or service not known")
    monkeypatch.setattr(metrics.urllib.request, "urlopen", raise_dns)
    with pytest.raises(StatusError) as caught:
        Prometheus("https://p", "1", "t").query("up", AT)
    assert caught.value.code == errors.METRICS_UNREACHABLE
    assert caught.value.code.endswith(".r")
    assert "losing days permanently" in caught.value.detail


def test_a_successful_real_transport_returns_the_body(monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"status": "success", "data": {"result": ["x"]}}).encode()

    monkeypatch.setattr(metrics.urllib.request, "urlopen", lambda request, timeout: FakeResponse())
    assert Prometheus("https://p", "1", "t").query("up", AT) == ["x"]
