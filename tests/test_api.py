"""Preuve de l'API HTTP (FastAPI TestClient) — SANS réseau ni LLM réel.

Base SQLite jetable (fixtures `tmp_db`/`seed_offers`) injectée via `dependency_overrides` ;
la génération CV/lettre est STUBÉE (aucun appel Mistral, aucun Typst). Couvre : liste +
filtres, détail, statut, bascule favori, édition de description, et les cas d'erreur
(404 offre inconnue, génération en échec -> 502 jamais un faux 200).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_generator, get_offers_service
from jobtool.api.generation import GenerationOutcome
from jobtool.api.main import app
from jobtool.services.offers_service import OffersService


class StubGenerator:
    """Générateur factice : renvoie l'issue programmée, sans aucun appel réel."""

    def __init__(self, cv=None, letter=None):
        self._cv = cv or GenerationOutcome(error="stub: non configuré")
        self._letter = letter or GenerationOutcome(error="stub: non configuré")

    def generate_cv(self, offer, template: str = "classic") -> GenerationOutcome:
        return self._cv

    def generate_letter(self, offer) -> GenerationOutcome:
        return self._letter


@pytest.fixture
def api(seed_offers, tmp_db):
    """(client, jobs) : base de fixture injectée dans l'app ; overrides nettoyés après."""
    jobs = seed_offers(tmp_db)
    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    client = TestClient(app)
    yield client, jobs
    app.dependency_overrides.clear()


def _set_generator(**kwargs) -> None:
    app.dependency_overrides[get_generator] = lambda: StubGenerator(**kwargs)


# --- Liste + filtres --------------------------------------------------------


def test_list_all_offers(api):
    client, _ = api
    r = client.get("/offers")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 4, "4 offres seedées"
    # Contrat de champs exposés au front.
    assert set(body[0]) == {
        "id",
        "titre",
        "entreprise",
        "lieu",
        "source",
        "date",
        "statut",
        "favorite",
        "description",
        "url",
        "applied_at",
        "hidden_at",
        # Champs normalisés et provenance (dédoublonnage entre sources).
        "contract_kind",
        "work_time",
        "country",
        "region",
        "department",
        "city",
        "place_precision",
        "sources",
        "expired",
        "private",
    }


def test_list_exposes_url(api):
    """`url` est dans le RÉSUMÉ : le front affiche le lien sans re-GET le détail (A2)."""
    client, jobs = api
    by_id = {o["id"]: o for o in client.get("/offers").json()}
    for job in jobs:
        assert by_id[job.dedup_hash]["url"] == job.url
        assert job.url, (
            "les offres seedées ont bien une URL (sinon le test ne prouve rien)"
        )


def test_list_filters(api):
    client, _ = api
    assert len(client.get("/offers", params={"status": "new"}).json()) == 2
    assert len(client.get("/offers", params={"keyword": "data"}).json()) == 2
    assert len(client.get("/offers", params={"location": "paris"}).json()) == 2
    # Filtre favori : aucun favori au départ.
    assert client.get("/offers", params={"favorite": True}).json() == []


def test_invalid_status_filter_is_422(api):
    client, _ = api
    assert client.get("/offers", params={"status": "pas_un_statut"}).status_code == 422


def test_favorites_filter_is_status_agnostic(api):
    """favorite=true renvoie le favori quel que soit son statut (même 'applied').

    Contrat dont dépend l'écran Postuler : la liste de gauche charge tous les favoris, puis
    écarte côté front ceux déjà postulés.
    """
    client, jobs = api
    h = jobs[0].dedup_hash
    client.patch(f"/offers/{h}/favorite", json={"favorite": True})
    client.patch(f"/offers/{h}/status", json={"status": "applied"})
    favs = client.get("/offers", params={"favorite": True}).json()
    assert [o["id"] for o in favs] == [h]
    assert favs[0]["statut"] == "applied", (
        "favori postulé toujours listé par favorite=true"
    )


# --- Tri par date de publication (lot B) ------------------------------------


@pytest.fixture
def dated_api(seed_dated_offers, tmp_db):
    """Client sur une base aux dates de PUBLICATION distinctes (dont une offre sans date)."""
    seed_dated_offers(tmp_db)
    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_order_by_posted_date(dated_api):
    """?order=posted_date : plus récemment publiées d'abord, sans date en queue."""
    body = dated_api.get("/offers", params={"order": "posted_date"}).json()
    assert [o["titre"] for o in body] == [
        "Publiée hier",
        "Publiée le mois dernier",
        "Publiée il y a un an",
        "Sans date de publication",
    ]


def test_list_default_order_unchanged(dated_api):
    """Sans `order`, la réponse garde l'ordre historique (fetched_date DESC)."""
    body = dated_api.get("/offers").json()
    assert body[0]["titre"] == "Sans date de publication", (
        "comportement actuel préservé"
    )


def test_list_invalid_order_is_422(dated_api):
    """Un `order` hors liste blanche est refusé par la validation, pas passé au SQL."""
    r = dated_api.get("/offers", params={"order": "posted_date; DROP TABLE jobs"})
    assert r.status_code == 422


# --- Filtre de fraîcheur (lot C0) -------------------------------------------


def test_list_max_age_days_filters(dated_api):
    """`max_age_days` restreint sur la date de PUBLICATION ; absent = aucun filtre."""
    assert len(dated_api.get("/offers").json()) == 4, "absent -> toute la base"
    recent = dated_api.get("/offers", params={"max_age_days": 7}).json()
    assert [o["titre"] for o in recent] == ["Publiée hier"]


def test_list_max_age_days_and_order_stack(dated_api):
    """Filtre ET tri s'empilent : « 60 j » restreint, « publication » ordonne l'intérieur."""
    body = dated_api.get(
        "/offers", params={"max_age_days": 60, "order": "posted_date"}
    ).json()
    assert [o["titre"] for o in body] == ["Publiée hier", "Publiée le mois dernier"]


def test_list_max_age_days_rejects_nonsense(dated_api):
    """Un âge négatif ou non numérique est une entrée invalide -> 422."""
    absurde = dated_api.get("/offers", params={"max_age_days": "beaucoup"})
    assert absurde.status_code == 422
    assert dated_api.get("/offers", params={"max_age_days": -3}).status_code == 422


# --- Détail + 404 -----------------------------------------------------------


def test_get_offer_detail(api):
    client, jobs = api
    target = jobs[0]
    r = client.get(f"/offers/{target.dedup_hash}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == target.dedup_hash
    assert body["titre"] == target.title
    assert "contract_type" in body and "url" in body, (
        "le détail expose les champs étendus"
    )


def test_get_unknown_offer_is_404(api):
    client, _ = api
    assert client.get("/offers/inconnu-xyz").status_code == 404


# --- Statut -----------------------------------------------------------------


def test_update_status(api):
    client, jobs = api
    target = jobs[0]
    r = client.patch(f"/offers/{target.dedup_hash}/status", json={"status": "applied"})
    assert r.status_code == 200 and r.json()["statut"] == "applied"
    # Persistance : relecture via le détail.
    assert client.get(f"/offers/{target.dedup_hash}").json()["statut"] == "applied"


def test_update_status_unknown_is_404(api):
    client, _ = api
    r = client.patch("/offers/inconnu/status", json={"status": "seen"})
    assert r.status_code == 404


# --- Favori (bascule + force) -----------------------------------------------


def test_toggle_favorite(api):
    client, jobs = api
    h = jobs[0].dedup_hash
    assert client.get(f"/offers/{h}").json()["favorite"] is False

    # Sans corps -> bascule à True.
    assert client.patch(f"/offers/{h}/favorite").json()["favorite"] is True
    # Re-bascule -> False.
    assert client.patch(f"/offers/{h}/favorite").json()["favorite"] is False
    # Avec corps -> force la valeur.
    assert (
        client.patch(f"/offers/{h}/favorite", json={"favorite": True}).json()[
            "favorite"
        ]
        is True
    )
    # Le filtre favori voit bien l'offre désormais.
    assert len(client.get("/offers", params={"favorite": True}).json()) == 1


def test_favorite_unknown_is_404(api):
    client, _ = api
    assert client.patch("/offers/inconnu/favorite").status_code == 404


# --- Description (chantier backend de cette tâche) --------------------------


def test_update_description(api):
    client, jobs = api
    h = jobs[0].dedup_hash
    r = client.patch(
        f"/offers/{h}/description", json={"description": "Complétée à la main"}
    )
    assert r.status_code == 200 and r.json()["description"] == "Complétée à la main"
    assert client.get(f"/offers/{h}").json()["description"] == "Complétée à la main"


def test_update_description_unknown_is_404(api):
    client, _ = api
    r = client.patch("/offers/inconnu/description", json={"description": "x"})
    assert r.status_code == 404


# --- Génération CV / lettre (stubée) ----------------------------------------


def test_generate_cv_success_returns_pdf(api, tmp_path):
    client, jobs = api
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    _set_generator(cv=GenerationOutcome(pdf_path=str(pdf)))

    r = client.post(f"/offers/{jobs[0].dedup_hash}/generate/cv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_generate_letter_success_returns_pdf(api, tmp_path):
    client, jobs = api
    pdf = tmp_path / "lettre.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    _set_generator(letter=GenerationOutcome(pdf_path=str(pdf)))

    r = client.post(f"/offers/{jobs[0].dedup_hash}/generate/letter")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_generate_cv_failure_is_502_with_reason(api):
    client, jobs = api
    _set_generator(cv=GenerationOutcome(error="génération : clé Mistral absente"))

    r = client.post(f"/offers/{jobs[0].dedup_hash}/generate/cv")
    assert r.status_code == 502, "échec -> 502, jamais un faux 200"
    body = r.json()
    assert body["kind"] == "cv"
    assert body["offer_id"] == jobs[0].dedup_hash
    assert "clé Mistral absente" in body["error"]


def test_generate_unknown_offer_is_404(api):
    client, _ = api
    _set_generator(cv=GenerationOutcome(pdf_path="/never/used.pdf"))
    assert client.post("/offers/inconnu/generate/cv").status_code == 404


# --- Front statique servi en même origine -----------------------------------
# Le CÂBLAGE du front (appels à l'API, jeton, gestes, génération…) est vérifié dans
# tests/test_front.py, sur les modules réellement servis. Ici : la façon dont l'API sert
# ces fichiers.


def test_front_index_served_at_root():
    """La racine sert l'index du front (même origine que l'API, pas de CORS)."""
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="app">' in r.text, "c'est bien index.html du front"


def test_front_assets_are_revalidated_not_cached():
    """Le front est servi en `no-cache` : le navigateur revalide au lieu de resservir sa copie.

    Sans cette en-tête, StaticFiles n'envoie qu'un etag/last-modified et les navigateurs
    appliquent une fraîcheur HEURISTIQUE : une modif du JS peut rester invisible.
    """
    r = TestClient(app).get("/js/app.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag"), "l'etag reste présent : la revalidation coûte un 304"


def test_front_revalidation_returns_304():
    """Revalidation conditionnelle : contenu inchangé -> 304 (pas de re-téléchargement)."""
    client = TestClient(app)
    etag = client.get("/js/app.js").headers["etag"]
    r = client.get("/js/app.js", headers={"If-None-Match": etag})
    assert r.status_code == 304


def test_front_index_is_revalidated():
    """Même règle pour index.html (servi via html=True à la racine)."""
    r = TestClient(app).get("/")
    assert r.headers["cache-control"] == "no-cache"


def test_front_assets_served():
    """Le point d'entrée est un module ES : le navigateur résout les imports sans build."""
    r = TestClient(app).get("/js/app.js")
    assert r.status_code == 200
    assert 'type="module"' in TestClient(app).get("/").text, (
        "point d'entrée en module ES"
    )


def test_api_routes_not_shadowed_by_front(api):
    """Le mount '/' ne masque pas les routes API : /offers répond toujours en JSON."""
    client, _ = api
    r = client.get("/offers", params={"status": "new"})
    assert r.status_code == 200 and isinstance(r.json(), list)
