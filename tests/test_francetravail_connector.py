"""Connecteur France Travail, sans réseau : codes de réponse de la recherche."""

from __future__ import annotations

from jobtool.aggregator._http import RateLimiter
from jobtool.aggregator.francetravail.connector import FranceTravailConnector
from jobtool.aggregator.models import SearchCriteria


class _Auth:
    def get_token(self) -> str:
        return "jeton"


def _conn(session):
    return FranceTravailConnector(
        _Auth(),  # type: ignore[arg-type]
        session=session,
        rate_limiter=RateLimiter(max_per_second=1000, sleep=lambda _d: None),
    )


def test_204_means_no_result_not_an_error(fake_session):
    """Constaté sur une vraie collecte : une recherche sans résultat répond 204."""
    conn = _conn(fake_session([(204, "")]))
    assert conn.fetch(SearchCriteria(mots_cles="alten", max_results=40)) == []
    assert conn.errors == []


def test_other_statuses_are_still_errors(fake_session):
    conn = _conn(fake_session([(500, {"message": "panne"})]))
    assert conn.fetch(SearchCriteria(mots_cles="data", max_results=40)) == []
    assert conn.errors and "500" in conn.errors[0]
