"""Pile « Découvrir » (GET /offers/stack) — de bout en bout, sur une VRAIE base SQLite.

Aucun mock de la couche d'écriture : les gestes « j'aime » et « masquer » passent par les
routes existantes (PATCH favorite / status), et la pile est relue depuis la base. C'est la
seule façon de prouver qu'une offre traitée ne revient pas — un test qui mockerait
l'écriture ne dirait rien de ce qui est réellement écrit.

La base et l'identité sont surchargées au niveau de `get_offers_db` et
`get_current_user_id` : les services, eux, sont construits par le vrai graphe `Depends`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator import storage
from jobtool.aggregator.matching import MatchCriteria
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.api import deps
from jobtool.api.main import app
from jobtool.core.db import ensure_user
from jobtool.services.discovery_service import DiscoveryService


def _job(native_id, title, *, location="75 - Paris", contract="CDI", age_days=1):
    posted = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    return raw_to_job(
        RawJob(
            source="france_travail",
            native_id=native_id,
            title=title,
            company="ACME",
            location=location,
            contract_type=contract,
            description=f"Description de {title}.",
            url=f"https://exemple.fr/{native_id}",
            posted_date=posted,
        )
    )


@pytest.fixture
def pile(tmp_path):
    """(client, ids par native_id, user_id, db) — un compte peuplé de 5 offres variées."""
    db = str(tmp_path / "pile.db")
    user_id = uuid.uuid4()
    jobs = [
        _job("DS", "Data Scientist"),
        _job("DE", "Data Engineer", location="92 - Nanterre"),
        _job("DA", "Data Analyst", contract="CDD - 12 Mois", age_days=20),
        _job("CPT", "Comptable", location="13 - Marseille", age_days=60),
        _job("OLD", "Data Scientist senior", age_days=90),
    ]
    conn = storage.connect(db)
    ensure_user(conn, user_id, email="moi@exemple.fr", name="moi")
    conn.commit()
    storage.upsert_jobs(conn, user_id, jobs)
    conn.close()

    app.dependency_overrides[deps.get_offers_db] = lambda: db
    app.dependency_overrides[deps.get_current_user_id] = lambda: user_id
    client = TestClient(app)
    yield client, {j.native_id: j.dedup_hash for j in jobs}, user_id, db
    app.dependency_overrides.clear()


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [card["id"] for card in resp.json()]


def test_stack_is_sorted_by_score_and_exposes_breakdown(pile):
    client, ids, *_ = pile
    r = client.get(
        "/offers/stack", params={"keyword": "data scientist", "departments": "75"}
    )
    cards = r.json()
    scores = [c["score"] for c in cards]
    assert scores == sorted(scores, reverse=True)
    assert cards[0]["id"] == ids["DS"]
    assert all(1 <= s <= 99 for s in scores)
    top = cards[0]
    assert {
        "titre",
        "entreprise",
        "lieu",
        "contract_type",
        "url",
        "date",
        "score",
    } <= set(top)
    assert [c["key"] for c in top["criteres"]] == ["keywords", "location", "freshness"]
    assert sum(c["max_points"] for c in top["criteres"]) == pytest.approx(100, abs=0.2)
    assert r.headers["X-Total-Count"] == str(len(cards))


def test_stack_route_is_not_captured_by_offer_id(pile):
    """`/offers/stack` est déclarée avant `/offers/{offer_id}` : pas un 404 « offre introuvable »."""
    client, *_ = pile
    assert client.get("/offers/stack").status_code == 200


def test_liked_and_hidden_offers_leave_the_stack(pile):
    client, ids, *_ = pile
    params = {"keyword": "data"}
    before = _ids(client.get("/offers/stack", params=params))
    assert ids["DS"] in before and ids["DE"] in before

    # Swipe droite = favori ; swipe gauche = masquée. Mêmes routes que la page « Offres ».
    assert (
        client.patch(
            f"/offers/{ids['DS']}/favorite", json={"favorite": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(f"/offers/{ids['DE']}/status", json={"status": "seen"}).status_code
        == 200
    )

    after = _ids(client.get("/offers/stack", params=params))
    assert ids["DS"] not in after
    assert ids["DE"] not in after

    # Cohérence avec la page « Offres » : l'état écrit est celui qu'elle lit.
    favs = [o["id"] for o in client.get("/offers", params={"favorite": "true"}).json()]
    hidden = [o["id"] for o in client.get("/offers", params={"status": "seen"}).json()]
    assert favs == [ids["DS"]]
    assert hidden == [ids["DE"]]


def test_undo_puts_the_offer_back(pile):
    client, ids, *_ = pile
    client.patch(f"/offers/{ids['DS']}/favorite", json={"favorite": True})
    client.patch(f"/offers/{ids['DE']}/status", json={"status": "seen"})
    # Annuler = forcer l'état inverse (jamais une bascule, qui dépendrait de l'ordre).
    client.patch(f"/offers/{ids['DS']}/favorite", json={"favorite": False})
    client.patch(f"/offers/{ids['DE']}/status", json={"status": "new"})
    stack = _ids(client.get("/offers/stack", params={"keyword": "data"}))
    assert ids["DS"] in stack and ids["DE"] in stack


def test_applied_offers_are_not_proposed(pile):
    client, ids, _user, db = pile
    conn = storage.connect(db)
    storage.set_status(conn, _user, ids["DA"], JobStatus.applied)
    conn.close()
    assert ids["DA"] not in _ids(client.get("/offers/stack"))


def test_limit_exclude_and_remaining_count(pile):
    """Le préchargement demande la suite en excluant ce qui est déjà affiché."""
    client, *_ = pile
    params = {"keyword": "data", "limit": 2}
    first = client.get("/offers/stack", params=params)
    first_ids = _ids(first)
    total = int(first.headers["X-Total-Count"])
    assert len(first_ids) == 2

    nxt = client.get("/offers/stack", params={**params, "exclude": first_ids})
    next_ids = _ids(nxt)
    assert not set(first_ids) & set(next_ids)
    assert int(nxt.headers["X-Total-Count"]) == total - 2

    full = _ids(client.get("/offers/stack", params={"keyword": "data", "limit": 50}))
    assert full[:4] == first_ids + next_ids


def test_threshold_drops_irrelevant_offers(pile):
    client, ids, *_ = pile
    stack = _ids(
        client.get(
            "/offers/stack",
            params={"keyword": "data", "departments": "75", "contract": "cdi"},
        )
    )
    # Ni mot-clé, ni lieu, ni contrat, et vieille de 60 jours : sous le seuil.
    assert ids["CPT"] not in stack


def test_max_age_days_is_a_hard_filter(pile):
    client, ids, *_ = pile
    stack = _ids(
        client.get("/offers/stack", params={"max_age_days": 30, "keyword": "data"})
    )
    assert ids["OLD"] not in stack and ids["DS"] in stack


def test_invalid_contract_is_rejected(pile):
    client, *_ = pile
    assert client.get("/offers/stack", params={"contract": "cdx"}).status_code == 422


def test_stack_shares_the_catalogue_but_not_the_state(pile):
    """Le catalogue est COMMUN : un autre compte voit la même pile. Ce qu'un compte en
    fait (masquer, aimer) reste à lui et ne retire rien de la pile de l'autre."""
    _client, ids, user_id, db = pile
    other = uuid.uuid4()
    conn = storage.connect(db)
    ensure_user(conn, other, email="autre@exemple.fr", name="autre")
    conn.commit()
    storage.set_status(conn, other, ids["DS"], JobStatus.seen)
    conn.close()
    theirs = DiscoveryService(db, other).stack(MatchCriteria(), limit=50, min_percent=1)
    mine = DiscoveryService(db, user_id).stack(MatchCriteria(), limit=50, min_percent=1)
    assert ids["DS"] not in {r.job.dedup_hash for r in theirs.items}
    assert {r.job.dedup_hash for r in mine.items} == set(ids.values())

