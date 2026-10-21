"""Collecte en arrière-plan (POST /aggregate) et recherche instantanée (GET /offers).

JAMAIS de réseau réel : seuls les CONNECTEURS sont remplacés. Le pipeline, le catalogue,
le suivi des collectes et la lecture tournent pour de vrai — c'est ce qui est ÉCRIT et
RELU qu'on vérifie (project_ctx.md : un test qui remplace l'écriture ne prouve rien).

Le TestClient exécute les tâches d'arrière-plan juste après avoir rendu la réponse :
la route répond 202 AVANT la collecte, et le suivi la montre terminée ensuite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator import runs, storage
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.api.main import app
from jobtool.core.db import ensure_user

client = TestClient(app)


class _StubConnector(Connector):
    """Connecteur sans réseau : renvoie des offres présètes (et d'éventuelles erreurs)."""

    def __init__(self, source, raws, errors=()):
        super().__init__()
        self.source = source
        self._raws = raws
        self._errors = list(errors)

    def fetch(self, criteria):
        self.errors = list(self._errors)
        return list(self._raws)


def _raw(native_id, title, *, location="75 - Paris", source="adzuna", company="ACME"):
    return RawJob(
        source=source,
        native_id=native_id,
        title=title,
        company=company,
        location=location,
        url=f"https://exemple.fr/offres/{native_id}",
        description="Une vraie offre, écrite en base.",
        posted_date="2026-09-15T00:00:00Z",
    )


@pytest.fixture
def collecte(monkeypatch, tmp_path):
    """API câblée sur une base jetable, connecteurs remplacés, comptes réels en base."""
    from jobtool.api import deps
    from jobtool.services import aggregation_service

    connectors = {"list": [_StubConnector("adzuna", [_raw("o1", "Ingénieur data")])]}
    monkeypatch.setattr(
        aggregation_service,
        "build_connectors",
        lambda names, ctx: (list(connectors["list"]), []),
    )
    monkeypatch.setenv("JOBTOOL_AGG_OUT", str(tmp_path / "offres"))
    db_file = tmp_path / "offres.db"
    app.dependency_overrides[deps.get_offers_db] = lambda: str(db_file)

    def account(email):
        user_id = uuid.uuid4()
        conn = storage.connect(db_file)
        try:
            ensure_user(conn, user_id, email=email, name=email)
            conn.commit()
        finally:
            conn.close()
        return user_id

    def as_user(user_id):
        app.dependency_overrides[deps.get_current_user_id] = lambda: user_id

    def collect(user_id, expected=202):
        as_user(user_id)
        r = client.post("/aggregate")
        assert r.status_code == expected, r.text
        return r

    def offers_of(user_id, **params):
        as_user(user_id)
        r = client.get("/offers", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def no_cooldown():
        monkeypatch.setenv("JOBTOOL_COLLECTION_COOLDOWN_MINUTES", "0")

    yield SimpleNamespace(
        db=db_file,
        connectors=connectors,
        account=account,
        as_user=as_user,
        collect=collect,
        offers_of=offers_of,
        no_cooldown=no_cooldown,
    )
    app.dependency_overrides.clear()


# --- Collecte en arrière-plan ---------------------------------------------------------
def test_post_returns_202_then_status_shows_the_finished_collection(collecte):
    me = collecte.account("moi@exemple.fr")
    r = collecte.collect(me)
    body = r.json()
    assert body["status"] == "running", "la route rend la main AVANT la collecte"
    assert body["trigger"] == "api"
    assert [s["source"] for s in body["sources"]] == [
        "adzuna",
        "france_travail",
        "smartrecruiters",
        "workday",
    ]

    status = client.get(f"/aggregate/{body['id']}").json()
    assert status["status"] == "done"
    assert status["summary"]["new_offers"] == 1
    assert status["summary"]["by_level"] == {"nouvelle": 1}
    adzuna = next(s for s in status["sources"] if s["source"] == "adzuna")
    assert adzuna["status"] == "done" and adzuna["fetched"] == 1
    assert client.get("/aggregate/latest").json()["id"] == body["id"]


def test_latest_is_null_before_any_collection_and_unknown_run_is_404(collecte):
    collecte.as_user(collecte.account("moi@exemple.fr"))
    assert client.get("/aggregate/latest").json() is None
    assert client.get("/aggregate/nimporte").status_code == 404


def test_source_errors_are_counted_as_errors_not_sources(collecte):
    collecte.connectors["list"] = [
        _StubConnector(
            "adzuna",
            [_raw("o1", "Ingénieur data")],
            errors=["page 2 : timeout", "page 3 : timeout"],
        )
    ]
    r = collecte.collect(collecte.account("moi@exemple.fr"))
    status = client.get(f"/aggregate/{r.json()['id']}").json()
    adzuna = next(s for s in status["sources"] if s["source"] == "adzuna")
    assert adzuna["errors"] == ["page 2 : timeout", "page 3 : timeout"]
    assert status["error_count"] == 2, "deux ERREURS, sur une seule source"
    assert status["status"] == "done", "une source en erreur n'échoue pas la collecte"


def test_a_crashing_collection_is_marked_failed(collecte, monkeypatch):
    from jobtool.aggregator import pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("panne du catalogue")

    monkeypatch.setattr(pipeline, "run_collection", boom)
    collecte.as_user(collecte.account("moi@exemple.fr"))
    tolerant = TestClient(app, raise_server_exceptions=False)
    run_id = tolerant.post("/aggregate").json()["id"]
    status = tolerant.get(f"/aggregate/{run_id}").json()
    assert status["status"] == "failed"
    assert any("panne du catalogue" in e for e in status["errors"])


# --- Verrou -------------------------------------------------------------------------------
def test_a_second_collection_within_15_minutes_is_refused_with_retry_after(collecte):
    me = collecte.account("moi@exemple.fr")
    first = collecte.collect(me).json()["id"]
    r = collecte.collect(me, expected=429)
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["detail"]["run_id"] == first


def test_a_running_collection_blocks_another_one(collecte):
    collecte.no_cooldown()
    conn = storage.connect(collecte.db)
    try:
        running = runs.start_run(conn, trigger="cli", sources=["adzuna"])
    finally:
        conn.close()
    r = collecte.collect(collecte.account("moi@exemple.fr"), expected=409)
    assert r.json()["detail"]["run_id"] == running, "l'interface peut la suivre"


def test_the_cli_is_not_subject_to_the_cooldown(collecte):
    from jobtool.services.aggregation_service import run_aggregation

    collecte.collect(collecte.account("moi@exemple.fr"))
    report = run_aggregation(db=str(collecte.db), export=False)
    assert report.run_id is not None


# --- Catalogue commun, recherche sans masquage --------------------------------------------
def test_collected_offers_are_visible_to_every_account_without_state(collecte):
    me = collecte.account("moi@exemple.fr")
    collecte.collect(me)
    voisin = collecte.account("voisin@exemple.fr")
    for user in (me, voisin):
        offers = collecte.offers_of(user, status="new")
        assert [o["titre"] for o in offers] == ["Ingénieur data"]
    conn = storage.connect(collecte.db)
    try:
        assert storage.count_by_status(conn, voisin) == {"new": 1}
        from sqlalchemy import func, select

        from jobtool.core.db import user_offers

        states = conn.execute(select(func.count()).select_from(user_offers)).scalar()
        assert states == 0, "une collecte ne crée l'état de personne"
    finally:
        conn.close()


def test_searching_masks_nothing_and_collections_accumulate(collecte):
    collecte.no_cooldown()
    me = collecte.account("moi@exemple.fr")
    collecte.collect(me)
    # Plusieurs recherches : rien ne change d'état.
    assert collecte.offers_of(me, q="ingénieur") and not collecte.offers_of(me, q="zzz")
    collecte.connectors["list"] = [
        _StubConnector("adzuna", [_raw("o2", "Chef de projet")])
    ]
    collecte.collect(me)
    titles = {o["titre"] for o in collecte.offers_of(me, status="new")}
    assert titles == {"Ingénieur data", "Chef de projet"}, (
        "la collecte suivante AJOUTE au catalogue ; rien n'a été masqué"
    )
    assert collecte.offers_of(me, status="seen") == []


def test_only_explicit_actions_change_an_offer_for_one_account(collecte):
    me = collecte.account("moi@exemple.fr")
    voisin = collecte.account("voisin@exemple.fr")
    collecte.collect(me)
    offer_id = collecte.offers_of(me)[0]["id"]
    collecte.as_user(me)
    assert client.patch(
        f"/offers/{offer_id}/status", json={"status": "seen"}
    ).is_success
    assert client.patch(f"/offers/{offer_id}/favorite").is_success
    assert collecte.offers_of(me, status="new") == []
    assert [o["id"] for o in collecte.offers_of(me, status="seen")] == [offer_id]
    assert [o["id"] for o in collecte.offers_of(voisin, status="new")] == [offer_id], (
        "le geste de l'un ne masque rien chez l'autre"
    )


# --- Filtres de recherche (champs normalisés) ----------------------------------------------
def test_search_filters_on_normalized_place_and_contract(collecte):
    collecte.connectors["list"] = [
        _StubConnector(
            "adzuna",
            [
                _raw("a", "Data scientist - CDI", location="75 - Paris"),
                _raw("b", "Stage data", location="Lyon (69)"),
                _raw("c", "Data engineer CDI", location=""),
                _raw("d", "Comptable CDI", location="92 - Nanterre"),
            ],
        )
    ]
    me = collecte.account("moi@exemple.fr")
    collecte.collect(me)

    def titles(**params):
        return sorted(o["titre"] for o in collecte.offers_of(me, **params))

    assert titles(q="data") == [
        "Data engineer CDI",
        "Data scientist - CDI",
        "Stage data",
    ]
    assert titles(departments="75,92") == ["Comptable CDI", "Data scientist - CDI"]
    assert titles(departments="75", include_unknown_location="true") == [
        "Data engineer CDI",
        "Data scientist - CDI",
    ]
    assert titles(contract="stage") == ["Stage data"]
    assert titles(q="data", contract=["cdi", "stage"], country="FR") == [
        "Data scientist - CDI",
        "Stage data",
    ]
    offer = collecte.offers_of(me, departments="69")[0]
    assert (offer["department"], offer["city"], offer["contract_kind"]) == (
        "69",
        "Lyon",
        "stage",
    )
    assert offer["sources"] == [
        {"source": "adzuna", "url": "https://exemple.fr/offres/b"}
    ]


def test_invalid_contract_filter_is_422(collecte):
    collecte.as_user(collecte.account("moi@exemple.fr"))
    assert client.get("/offers", params={"contract": "cdx"}).status_code == 422


def test_hidden_offer_is_recoverable(collecte):
    me = collecte.account("moi@exemple.fr")
    collecte.collect(me)
    offer_id = collecte.offers_of(me)[0]["id"]
    conn = storage.connect(collecte.db)
    try:
        storage.set_status(conn, me, offer_id, JobStatus.seen)
        storage.set_status(conn, me, offer_id, JobStatus.new)
    finally:
        conn.close()
    assert [o["id"] for o in collecte.offers_of(me, status="new")] == [offer_id]
