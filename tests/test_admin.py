"""Écran d'administration : compteurs par compte, cloisonnement, et garde d'accès.

Deux propriétés y sont prouvées, et ce sont les deux qui comptent en multi-client :
  - le catalogue d'offres est MUTUALISÉ, mais chaque compte ne compte que les siennes ;
  - la route est refusée (403) à un compte non administrateur.

Aucun contenu n'est exposé par cet écran : le test vérifie donc aussi que la réponse ne
contient que des compteurs, jamais du texte d'offre ou de CV.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator import storage
from jobtool.aggregator.models import Job, JobStatus
from jobtool.api.deps import get_admin_service, get_current_user_id
from jobtool.api.main import app
from jobtool.core.db import (
    LOCAL_USER_ID,
    engine_for_sqlite_file,
    ensure_user,
    users,
)
from jobtool.services.admin_service import AdminService

OTHER_USER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _job(suffix: str) -> Job:
    return Job(
        id=f"id-{suffix}",
        source="france_travail",
        native_id=f"FT-{suffix}",
        title=f"Poste {suffix}",
        company="Acme",
        location="Paris",
        remote="inconnu",
        contract_type="CDI",
        salary=None,
        description="Description.",
        url=f"https://example.fr/{suffix}",
        posted_date="2026-07-01T08:00:00+00:00",
        fetched_date="2026-07-02T08:00:00+00:00",
        dedup_hash=f"h-{suffix}",
        status=JobStatus.new,
        favorite=False,
        applied_at=None,
        raw={},
    )


@pytest.fixture
def two_accounts(tmp_db):
    """Deux comptes sur la même base : le local (admin) et un autre (non admin).

    Le compte local reçoit 3 offres, l'autre 1 — dont 1 partagée, pour prouver que le
    catalogue est commun sans que les vues le soient.
    """
    engine = engine_for_sqlite_file(tmp_db)
    conn = storage.connect(tmp_db)
    try:
        ensure_user(conn, OTHER_USER, email="autre@jobtool.fr", name="Autre compte")
        conn.execute(
            users.update().where(users.c.id == LOCAL_USER_ID).values(is_admin=True)
        )
        for suffix in ("a", "b", "c"):
            storage.upsert_job(conn, LOCAL_USER_ID, _job(suffix))
        storage.upsert_job(conn, OTHER_USER, _job("a"))  # MÊME offre, autre compte
        storage.set_favorite(conn, OTHER_USER, "h-a", True)
        conn.commit()
    finally:
        conn.close()
    return engine


def test_counts_are_per_account_but_catalogue_is_shared(two_accounts):
    """Chaque compte compte SES offres ; le catalogue, lui, ne compte l'annonce qu'une fois."""
    overview = AdminService(two_accounts).overview()
    by_email = {a.email: a for a in overview.accounts}

    assert by_email["local@jobtool"].offers == 3
    assert by_email["autre@jobtool.fr"].offers == 1
    # 3 annonces distinctes, alors que la somme des vues fait 4 : l'offre « a » est partagée.
    assert overview.catalogue_offers == 3
    assert sum(a.offers for a in overview.accounts) == 4


def test_favorite_of_one_account_is_not_the_other_s(two_accounts):
    """Le favori est privé : c'est toute la raison d'être de la table `user_offers`."""
    by_email = {a.email: a for a in AdminService(two_accounts).overview().accounts}
    assert by_email["autre@jobtool.fr"].favorites == 1
    assert by_email["local@jobtool"].favorites == 0


def test_overview_exposes_counters_only(two_accounts):
    """Aucun contenu client dans la réponse : ni titre d'offre, ni description."""
    app.dependency_overrides[get_admin_service] = lambda: AdminService(two_accounts)
    app.dependency_overrides[get_current_user_id] = lambda: LOCAL_USER_ID
    try:
        body = TestClient(app).get("/admin/overview").json()
    finally:
        app.dependency_overrides.clear()

    blob = str(body)
    assert "Poste a" not in blob and "Description." not in blob
    assert body["accounts"][0]["offers"] == 3


def test_non_admin_is_refused(two_accounts):
    """Un compte ordinaire n'entre pas : 403, et rien du tout dans le corps."""
    app.dependency_overrides[get_admin_service] = lambda: AdminService(two_accounts)
    app.dependency_overrides[get_current_user_id] = lambda: OTHER_USER
    try:
        response = TestClient(app).get("/admin/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "accounts" not in response.json()


def test_catalogue_counts_the_shared_offers_not_the_views(two_accounts):
    """Le catalogue compte les ANNONCES connues, pas celles qu'un compte suit encore.

    Les deux coïncident tant que rien n'est purgé — c'est pourquoi le décompte a longtemps
    pu porter sur `user_offers` sans que rien ne proteste. Ils divergent dès la première
    purge : elle retire la ligne du compte et laisse l'annonce au catalogue mutualisé, d'où
    une prochaine recherche la ramènera sans la recollecter.
    """
    conn = two_accounts.connect()
    try:
        # « h-c » ne concerne que le compte local : on l'en retire, comme le ferait la purge.
        assert storage.forget_offer(conn, LOCAL_USER_ID, "h-c") is True
    finally:
        conn.close()

    overview = AdminService(two_accounts).overview()
    by_email = {a.email: a for a in overview.accounts}
    assert by_email["local@jobtool"].offers == 2, "le compte n'en suit plus que 2"
    assert overview.catalogue_offers == 3, (
        "l'annonce reste au catalogue : c'est ce qui évite de la recollecter"
    )


def test_single_user_station_is_admin(two_accounts):
    """Mono-poste : le propriétaire du poste est administrateur sans drapeau en base."""
    assert AdminService(two_accounts, single_user=True).is_admin(OTHER_USER)
    assert not AdminService(two_accounts).is_admin(OTHER_USER)


def test_local_mode_wiring_grants_admin(monkeypatch, tmp_path):
    """Sans DATABASE_URL, la dépendance réelle rend un service mono-poste."""
    from jobtool.api import deps
    from jobtool.settings import AggregatorSettings

    monkeypatch.setattr(deps, "get_engine", lambda: None)
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: AggregatorSettings(JOBTOOL_AGG_DB=tmp_path / "j.db"),
    )
    assert deps.get_admin_service().is_admin(LOCAL_USER_ID)


# --- /auth/me : ce que l'interface sait des droits du compte ------------------


def test_me_tells_the_interface_who_is_admin(two_accounts):
    """L'interface n'affiche Administration et Journal LLM qu'aux administrateurs."""
    app.dependency_overrides[get_admin_service] = lambda: AdminService(two_accounts)
    try:
        app.dependency_overrides[get_current_user_id] = lambda: LOCAL_USER_ID
        assert TestClient(app).get("/auth/me").json() == {"is_admin": True}
        app.dependency_overrides[get_current_user_id] = lambda: OTHER_USER
        assert TestClient(app).get("/auth/me").json() == {"is_admin": False}
    finally:
        app.dependency_overrides.clear()


def test_hiding_the_links_is_not_the_protection(two_accounts):
    """Même en appelant les routes directement, un non-admin reste refusé partout."""
    app.dependency_overrides[get_admin_service] = lambda: AdminService(two_accounts)
    app.dependency_overrides[get_current_user_id] = lambda: OTHER_USER
    try:
        client = TestClient(app)
        for path in ("/admin/overview", "/admin/llm-runs", "/admin/llm-runs/inconnu"):
            assert client.get(path).status_code == 403, path
    finally:
        app.dependency_overrides.clear()


def test_local_single_user_is_admin():
    """Mono-poste : le poste local est administrateur d'office (même règle que /admin)."""
    assert TestClient(app).get("/auth/me").json() == {"is_admin": True}
