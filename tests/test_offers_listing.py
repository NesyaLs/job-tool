"""Écran Offres : tri, filtres et pagination, tels que le front les demande vraiment.

Ces tests sont nés d'un constat d'usage : « pas de tri, les offres n'ont pas de rapport avec
la recherche, on récupère les résultats d'anciennes recherches ». Ils servent donc d'abord à
ÉTABLIR les faits — ce qui marche, ce qui ne marche pas, et ce qui marche mais ne fait pas ce
que l'utilisateur croyait.

Les dates sont ici toutes DISTINCTES et explicites : un tri qui ne trie pas passerait
inaperçu sur des offres publiées le même jour. Elles sont EXPRIMÉES EN ÉCART À MAINTENANT
(`NOW`), et non figées à une date d'écriture : `max_age_days` compare à `datetime.now()`,
donc un repère gelé rend la suite verte le jour où on l'écrit et rouge quelques semaines
plus tard, sans qu'une ligne de code ait bougé.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from jobtool.aggregator import storage
from jobtool.aggregator.models import RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.api.deps import get_offers_service
from jobtool.api.main import app
from jobtool.core.db import ensure_user
from jobtool.services.offers_service import OffersService

#: Repère de toutes les dates du fichier. Les écarts (`jours=…`) restent fixes et
#: lisibles ; c'est le point d'ancrage qui suit l'horloge.
NOW = datetime.now(UTC)


def _job(
    *, titre, entreprise="ACME", lieu="Paris", jours=0, source="adzuna", publiee=True
):
    """Une offre publiée il y a `jours` jours ; `publiee=False` = date inconnue."""
    return raw_to_job(
        RawJob(
            source=source,
            native_id=f"{titre}-{lieu}-{jours}",
            title=titre,
            company=entreprise,
            location=lieu,
            url=f"https://exemple.fr/{titre}-{jours}".replace(" ", "-"),
            description="Offre de test.",
            posted_date=(NOW - timedelta(days=jours)).isoformat() if publiee else None,
        ),
        fetched_date=(NOW - timedelta(hours=jours)).isoformat(),
    )


@pytest.fixture
def client(tmp_path):
    """API branchée sur une base jetable, avec un compte réel."""
    db = tmp_path / "offres.db"
    user_id = uuid.uuid4()
    conn = storage.connect(db)
    ensure_user(conn, user_id, email="moi@exemple.fr", name="moi")
    conn.commit()
    conn.close()

    def seed(jobs):
        conn = storage.connect(db)
        storage.upsert_jobs(conn, user_id, jobs)
        conn.close()

    app.dependency_overrides[get_offers_service] = lambda: OffersService(
        str(db), user_id
    )
    c = TestClient(app)
    c.seed = seed
    yield c
    app.dependency_overrides.clear()


def _titres(response):
    """Titres renvoyés — le corps reste une LISTE, paginé ou non."""
    assert response.status_code == 200, response.text
    return [o["titre"] for o in response.json()]


# --- Tri ---------------------------------------------------------------------


def test_sort_by_posted_date_puts_the_newest_first(client):
    """Le tri demandé par le front par défaut (`posted_date`)."""
    client.seed(
        [
            _job(titre="Ancienne", jours=30),
            _job(titre="Récente", jours=1),
            _job(titre="Intermédiaire", jours=10),
        ]
    )
    assert _titres(client.get("/offers?order=posted_date")) == [
        "Récente",
        "Intermédiaire",
        "Ancienne",
    ]


def test_offers_without_a_publication_date_come_last(client):
    """Une offre sans date connue ne doit pas passer devant une offre datée d'hier."""
    client.seed(
        [
            _job(titre="Sans date", publiee=False),
            _job(titre="Publiée hier", jours=1),
        ]
    )
    assert _titres(client.get("/offers?order=posted_date")) == [
        "Publiée hier",
        "Sans date",
    ]


def test_sort_by_fetched_date(client):
    client.seed(
        [
            _job(titre="Collectée avant", jours=5),
            _job(titre="Collectée après", jours=0),
        ]
    )
    assert _titres(client.get("/offers?order=fetched_date")) == [
        "Collectée après",
        "Collectée avant",
    ]


def test_unknown_sort_is_refused(client):
    """La liste blanche est typée : une valeur hors liste n'atteint jamais le SQL."""
    assert client.get("/offers?order=; DROP TABLE offers").status_code == 422


# --- Filtres -----------------------------------------------------------------


def test_keyword_filters_on_title_and_company(client):
    client.seed(
        [
            _job(titre="Ingénieur data", entreprise="Globex"),
            _job(titre="Comptable", entreprise="DataCorp"),
            _job(titre="Juriste", entreprise="Lex"),
        ]
    )
    trouves = _titres(client.get("/offers?keyword=data"))
    assert sorted(trouves) == ["Comptable", "Ingénieur data"], (
        "le mot-clé porte sur le titre ET l'entreprise"
    )


def test_location_filter(client):
    client.seed(
        [
            _job(titre="À Lyon", lieu="Lyon"),
            _job(titre="À Paris", lieu="Paris 15e"),
        ]
    )
    assert _titres(client.get("/offers?location=paris")) == ["À Paris"]


def test_max_age_days_keeps_only_recent_offers(client):
    client.seed(
        [
            _job(titre="Vieille", jours=40),
            _job(titre="Fraîche", jours=2),
        ]
    )
    assert _titres(client.get("/offers?max_age_days=7")) == ["Fraîche"]


def test_filter_and_sort_hold_together(client):
    """Le filtre ne doit pas défaire le tri : c'est la combinaison qu'utilise l'écran."""
    client.seed(
        [
            _job(titre="Data ancienne", jours=20),
            _job(titre="Data récente", jours=2),
            _job(titre="Autre métier", jours=1),
        ]
    )
    assert _titres(client.get("/offers?keyword=data&order=posted_date")) == [
        "Data récente",
        "Data ancienne",
    ]


def test_keyword_with_a_sql_wildcard_is_taken_literally(client):
    """`%` et `_` sont des jokers SQL : cherchés, ils ne doivent pas tout ramener."""
    client.seed(
        [
            _job(titre="Prime 100% variable"),
            _job(titre="Poste sans prime"),
        ]
    )
    assert _titres(client.get("/offers?keyword=100%25")) == ["Prime 100% variable"]


# --- Pagination --------------------------------------------------------------


def _page(client, **params):
    """(titres, total) d'une page — le total vient de l'en-tête `X-Total-Count`."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/offers?{query}")
    assert r.status_code == 200, r.text
    return [o["titre"] for o in r.json()], int(r.headers["X-Total-Count"])


def test_a_page_is_limited_and_announces_the_full_total(client):
    client.seed([_job(titre=f"Offre {i:02d}", jours=i) for i in range(40)])

    titres, total = _page(client, order="posted_date", limit=15, offset=0)

    assert len(titres) == 15, "la page est bornée"
    assert total == 40, "…mais le total annoncé reste celui de l'ensemble filtré"
    assert titres[0] == "Offre 00", "la page reste triée (publication la plus récente)"


def test_pages_tile_the_result_without_gap_or_overlap(client):
    """Le défaut classique de la pagination : une offre vue deux fois, ou jamais."""
    client.seed([_job(titre=f"Offre {i:02d}", jours=i) for i in range(40)])

    vus = []
    for offset in (0, 15, 30):
        titres, total = _page(client, order="posted_date", limit=15, offset=offset)
        vus.extend(titres)

    assert len(vus) == 40 == total
    assert len(set(vus)) == 40, "aucune offre n'apparaît sur deux pages"
    assert vus == sorted(vus), "l'ordre global est conservé d'une page à l'autre"


def test_pagination_is_stable_when_dates_are_identical(client):
    """Des offres publiées le même jour se répartissent proprement entre les pages.

    Attention à la portée de ce test : il s'exécute sur SQLite, dont le plan d'exécution
    est ici déterministe — il passe donc MÊME SANS le départage par `dedup_hash` (vérifié
    en le retirant). Ce n'est pas une raison pour l'enlever du code : sur Postgres, l'ordre
    de deux lignes ex æquo n'est garanti par rien, et c'est là qu'une offre sauterait une
    page pendant qu'une autre s'y répéterait. Le test documente l'intention et couvrirait
    la régression le jour où la suite tournera sur une vraie base (SQLite en test, Postgres en production).
    """
    client.seed([_job(titre=f"Ex aequo {i:02d}", jours=3) for i in range(30)])

    page1, _ = _page(client, order="posted_date", limit=15, offset=0)
    page2, _ = _page(client, order="posted_date", limit=15, offset=15)

    assert set(page1) & set(page2) == set(), "les deux pages sont disjointes"
    assert len(set(page1) | set(page2)) == 30, "et couvrent tout"


def test_the_total_counts_filtered_offers_not_the_whole_account(client):
    """« 15 sur 369 » alors que le filtre n'en garde que 12 serait un mensonge d'affichage."""
    client.seed(
        [_job(titre=f"Data {i}", jours=i) for i in range(12)]
        + [_job(titre=f"Autre {i}", jours=i) for i in range(28)]
    )
    titres, total = _page(client, keyword="Data", limit=15, offset=0)
    assert total == 12, "le total suit le filtre"
    assert len(titres) == 12


def test_last_page_may_be_partial(client):
    client.seed([_job(titre=f"Offre {i:02d}", jours=i) for i in range(20)])
    titres, total = _page(client, order="posted_date", limit=15, offset=15)
    assert len(titres) == 5 and total == 20


def test_offset_beyond_the_end_returns_an_empty_page(client):
    client.seed([_job(titre="Unique")])
    titres, total = _page(client, limit=15, offset=99)
    assert titres == [] and total == 1, "vide, mais le total reste juste"


def test_without_limit_everything_is_returned(client):
    """Compatibilité : les appelants qui ne paginent pas ne changent pas de comportement."""
    client.seed([_job(titre=f"Offre {i:02d}", jours=i) for i in range(20)])
    titres, total = _page(client, order="posted_date")
    assert len(titres) == 20 and total == 20
