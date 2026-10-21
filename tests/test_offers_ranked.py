"""Liste NOTÉE (GET /offers/ranked) et date de masquage — sur une VRAIE base SQLite.

La route sert les dossiers de l'interface : filtres DURS de `GET /offers`, score de la
pile « Découvrir ». On prouve les deux moitiés : ce qui est filtré ne revient pas, et ce
qui reste est noté (et trié par score quand on le demande), sans seuil qui cacherait des
offres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator import storage
from jobtool.aggregator.models import RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.api import deps
from jobtool.api.main import app
from jobtool.core.db import ensure_user


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
def catalogue(tmp_path):
    """(client, ids par native_id) — un compte et cinq offres variées."""
    db = str(tmp_path / "ranked.db")
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
    yield TestClient(app), {j.native_id: j.dedup_hash for j in jobs}
    app.dependency_overrides.clear()


def _cards(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_sorted_by_score_without_any_threshold(catalogue):
    client, ids = catalogue
    r = client.get("/offers/ranked", params={"keywords": "data scientist"})
    cards = _cards(r)
    scores = [c["score"] for c in cards]
    assert scores == sorted(scores, reverse=True)
    assert cards[0]["id"] == ids["DS"]
    # Aucune offre écartée : le Comptable, sans aucun mot-clé, reste dans la liste.
    assert len(cards) == 5 and r.headers["X-Total-Count"] == "5"
    assert ids["CPT"] in {c["id"] for c in cards}
    assert {"score", "criteres", "hidden_at"} <= set(cards[0])


def test_filters_are_hard_and_the_total_follows_them(catalogue):
    client, ids = catalogue
    r = client.get(
        "/offers/ranked",
        params={"keywords": "data", "departments": "75", "contract": "cdi"},
    )
    got = {c["id"] for c in _cards(r)}
    assert got == {ids["DS"], ids["OLD"]}, "Nanterre, Marseille et le CDD sont filtrés"
    assert r.headers["X-Total-Count"] == "2"

    r = client.get("/offers/ranked", params={"q": "analyst"})
    assert [c["id"] for c in _cards(r)] == [ids["DA"]]


def test_pagination_slices_the_ranked_list(catalogue):
    client, _ = catalogue
    full = _cards(client.get("/offers/ranked", params={"keywords": "data"}))
    page = client.get(
        "/offers/ranked", params={"keywords": "data", "limit": 2, "offset": 2}
    )
    assert [c["id"] for c in _cards(page)] == [c["id"] for c in full[2:4]]
    assert page.headers["X-Total-Count"] == "5"


def test_state_filters_serve_the_folders(catalogue):
    """Favoris, masquées : l'état du compte filtre comme sur GET /offers."""
    client, ids = catalogue
    client.patch(f"/offers/{ids['DE']}/favorite", json={"favorite": True})
    client.patch(f"/offers/{ids['CPT']}/status", json={"status": "seen"})

    fav = _cards(client.get("/offers/ranked", params={"status": "new", "favorite": True}))
    assert [c["id"] for c in fav] == [ids["DE"]]

    new = _cards(client.get("/offers/ranked", params={"status": "new", "favorite": False}))
    assert {c["id"] for c in new} == {ids["DS"], ids["DA"], ids["OLD"]}

    hidden = _cards(client.get("/offers/ranked", params={"status": "seen"}))
    assert [c["id"] for c in hidden] == [ids["CPT"]]


def test_sql_order_is_kept_when_requested(catalogue):
    client, ids = catalogue
    for native_id in ("OLD", "DS"):
        client.patch(f"/offers/{ids[native_id]}/status", json={"status": "applied"})
    cards = _cards(
        client.get("/offers/ranked", params={"status": "applied", "order": "applied_at"})
    )
    assert [c["id"] for c in cards][0] == ids["DS"], "dernière candidature en tête"
    assert all("score" in c for c in cards)


def test_unknown_order_is_refused(catalogue):
    client, _ = catalogue
    assert client.get("/offers/ranked", params={"order": "titre"}).status_code == 422


def test_ranked_route_is_not_captured_by_offer_id(catalogue):
    client, _ = catalogue
    assert client.get("/offers/ranked").status_code == 200


def test_hidden_at_is_set_on_hide_and_cleared_on_restore(catalogue):
    """La date de masquage est lisible (écran Masquées) et disparaît à la récupération."""
    client, ids = catalogue
    hidden = client.patch(f"/offers/{ids['DS']}/status", json={"status": "seen"}).json()
    assert hidden["hidden_at"], "la date de masquage est exposée"
    listed = {o["id"]: o for o in client.get("/offers", params={"status": "seen"}).json()}
    assert listed[ids["DS"]]["hidden_at"] == hidden["hidden_at"]

    restored = client.patch(f"/offers/{ids['DS']}/status", json={"status": "new"}).json()
    assert restored["hidden_at"] is None
