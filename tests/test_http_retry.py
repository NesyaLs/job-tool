"""Preuve du helper get_with_retry : retry 429/5xx, back-off, Retry-After, plafond.

Fakes locaux minimalistes (avec en-têtes, que la FakeSession partagée n'expose pas) et
`sleep` injecté -> on prouve le back-off SANS attente réelle.
"""

from __future__ import annotations

from jobtool.aggregator._http import get_with_retry


class _Resp:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}


class _Session:
    """Renvoie des réponses programmées (status, headers) ; compte les GET."""

    def __init__(self, outcomes: list[tuple[int, dict]]):
        self._outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        idx = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        status, hdrs = self._outcomes[idx]
        return _Resp(status, hdrs)


def test_success_first_try_no_sleep():
    sleeps: list[float] = []
    session = _Session([(200, {})])
    resp = get_with_retry(session, "http://x", sleep=sleeps.append)
    assert resp.status_code == 200
    assert session.calls == 1 and sleeps == [], "succès immédiat : 1 GET, aucun délai"


def test_retry_on_429_then_success_uses_backoff():
    sleeps: list[float] = []
    session = _Session([(429, {}), (200, {})])
    resp = get_with_retry(session, "http://x", sleep=sleeps.append, backoff_base=2.0)
    assert resp.status_code == 200 and session.calls == 2
    assert sleeps == [2.0], (
        "back-off exponentiel base*2**0 = 2.0 sur la 1re re-tentative"
    )


def test_retry_after_header_is_honored():
    sleeps: list[float] = []
    session = _Session([(503, {"Retry-After": "7"}), (200, {})])
    resp = get_with_retry(session, "http://x", sleep=sleeps.append, backoff_base=1.0)
    assert resp.status_code == 200
    assert sleeps == [7.0], "Retry-After prioritaire sur le back-off"


def test_exhausts_retries_returns_last_response():
    sleeps: list[float] = []
    session = _Session([(503, {})])  # toujours 503
    resp = get_with_retry(session, "http://x", sleep=sleeps.append, max_retries=3)
    assert resp.status_code == 503, "plafond atteint -> on renvoie la dernière réponse"
    assert session.calls == 4, "1 tentative + 3 re-tentatives"
    assert len(sleeps) == 3, "3 attentes (une par re-tentative)"


def test_non_retryable_status_returned_immediately():
    sleeps: list[float] = []
    session = _Session([(404, {})])
    resp = get_with_retry(session, "http://x", sleep=sleeps.append)
    assert resp.status_code == 404 and session.calls == 1 and sleeps == [], (
        "404 n'est pas réessayable : renvoyé tout de suite"
    )
