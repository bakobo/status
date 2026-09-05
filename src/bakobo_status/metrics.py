"""The thin layer that talks to Grafana Cloud's Prometheus, and nothing else.

Kept apart from :mod:`bakobo_status.rollup` so that every arithmetic decision about a published
number is testable without a network or a credential. This file is the only part that cannot be,
so it is deliberately small enough to read in one sitting and contains no arithmetic at all.

`urllib` rather than `requests` or `httpx`, because the tool's one durable property is that it has
no dependencies (see the README) and a nightly job is the last place to spend that. What `requests`
would buy here is a nicer exception hierarchy, and this module raises its own anyway.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import errors
from .errors import StatusError

DEFAULT_TIMEOUT = 60


def _auth_header(user_id: str, token: str) -> str:
    """Grafana Cloud's Prometheus reads basic auth, not a bearer token.

    The username is the numeric instance id from the Cloud Portal and the password is the access
    policy token. Getting this wrong yields a 401 that says nothing about which half was wrong,
    which is worth knowing before spending an evening on the token.
    """
    raw = f"{user_id}:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class Prometheus:
    """A read-only client for one Grafana Cloud Prometheus instance.

    ``transport`` exists so tests can supply responses. It defaults to a real HTTP call and is
    never a mock in production, which keeps the seam honest: the thing under test is the request
    this builds and the response it accepts, not a rehearsal of urllib.
    """

    def __init__(self, base_url: str, user_id: str, token: str, *, transport=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.token = token
        self._transport = transport or self._fetch

    def _fetch(self, url: str, headers: dict) -> bytes:
        request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https, ours
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            # A status is a refusal with an explanation; surface it rather than the generic
            # "HTTP Error 401", because the body usually names which credential was wrong.
            body = exc.read().decode("utf-8", "replace")[:400]
            raise StatusError(
                errors.METRICS_REFUSED,
                "Grafana refused the metrics query.",
                f"HTTP {exc.code} from {self.base_url}: {body}. A 401 here is usually the instance "
                "id rather than the token — the username is the numeric Prometheus id from the "
                "Cloud Portal, not the stack name.",
            ) from exc
        except urllib.error.URLError as exc:
            raise StatusError(
                errors.METRICS_UNREACHABLE,
                "Grafana's metrics endpoint could not be reached.",
                f"{self.base_url} is unreachable: {exc.reason}. Retryable — but a rollup that "
                "keeps failing is losing days permanently, so do not let it retry quietly.",
            ) from exc

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        raw = self._transport(url, {"Authorization": _auth_header(self.user_id, self.token)})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StatusError(
                errors.METRICS_REFUSED,
                "Grafana returned something that is not a query result.",
                f"The response from {self.base_url}{path} is not JSON: {exc}. This usually means "
                "the base URL points at the stack rather than at the Prometheus instance.",
            ) from exc
        if payload.get("status") != "success":
            raise StatusError(
                errors.METRICS_REFUSED,
                "Grafana rejected the query.",
                f"{payload.get('errorType', 'error')}: {payload.get('error', 'no reason given')}. "
                "The query is built in rollup.py; a parse error there is a defect here, not a "
                "credential problem.",
            )
        return payload["data"]

    def query(self, promql: str, at) -> list:
        """An instant query. Returns the vector result, which may legitimately be empty."""
        data = self._get(
            "/api/prom/api/v1/query",
            {"query": promql, "time": at.timestamp()},
        )
        return data.get("result", [])

    def query_range(self, promql: str, start, end, step: int) -> list:
        """A range query. Returns the matrix result, which may legitimately be empty."""
        data = self._get(
            "/api/prom/api/v1/query_range",
            {
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
        return data.get("result", [])
