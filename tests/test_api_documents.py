"""Preuve des routes /documents (retouche manuelle) — SANS LLM.

Le brouillon est semé directement (render_input d'exemple + relevé des textes d'origine) :
c'est possible parce qu'un brouillon = `render_input` + retouches, et que recompiler ne
demande jamais le modèle. Typst et poppler sont, eux, bien réels — c'est le sujet du test.

Couvre : ouverture d'un document existant, projection des chemins disque en URL, service des
artefacts (aperçu PNG, PDF), édition + recompilation + recontrôle, réinitialisation, et les
cas d'erreur (offre inconnue, document inconnu, id malformé -> jamais un chemin hors dossier).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_document_edit_service, get_offers_service
from jobtool.api.main import app
from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.documents.drafts import store
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.services.document_edit_service import (
    DocumentEditError,
    DocumentEditService,
)
from jobtool.services.offers_service import OffersService

BASE_YAML = Path("data/base.example.yaml")
DOC_ID = "0123456789abcdef"


@pytest.fixture
def documents_api(seed_offers, tmp_db, typst_out_dir, example_base, example_cv):
    """(client, offer_id) : offre seedée + brouillon de CV prêt à retoucher."""
    from jobtool.documents.cv.profiles import get_profile
    from jobtool.documents.cv.render import build_render_input

    jobs = seed_offers(tmp_db)
    offer_id = jobs[0].dedup_hash

    service = DocumentEditService(
        base_repo=YamlExperienceBase(BASE_YAML),
        drafts_db=typst_out_dir / "drafts.db",
        work_root=typst_out_dir / "docs",
        profile_key="",
    )
    render_input = build_render_input(example_base, example_cv, get_profile("compact"))
    timestamp = store.now_iso()
    draft = DocumentDraft(
        doc_id=DOC_ID,
        offer_id=offer_id,
        kind="cv",
        template="compact",
        render_input=render_input,
        created_at=timestamp,
        updated_at=timestamp,
    )
    originals = service._compile(
        draft, render_input, service._work_dir(DOC_ID) / "_original"
    )
    draft = draft.model_copy(
        update={"original_texts": {b.id: b.text for b in originals.fields}}
    )
    conn = store.connect(service._drafts_db)
    try:
        store.save_draft(conn, LOCAL_USER_ID, draft, profile_key="")
    finally:
        conn.close()

    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    app.dependency_overrides[get_document_edit_service] = lambda: service
    client = TestClient(app)
    yield client, offer_id
    app.dependency_overrides.clear()


# --- Ouverture / lecture ----------------------------------------------------


def test_open_resumes_the_existing_draft(documents_api):
    """POST /documents REPREND le brouillon de l'offre : aucune génération relancée."""
    client, offer_id = documents_api
    r = client.post("/documents", json={"offer_id": offer_id, "kind": "cv"})
    assert r.status_code == 200
    assert r.json()["doc_id"] == DOC_ID


def test_open_unknown_offer_is_404(documents_api):
    client, _ = documents_api
    r = client.post("/documents", json={"offer_id": "inconnue", "kind": "cv"})
    assert r.status_code == 404


def test_open_unknown_kind_is_422(documents_api):
    client, offer_id = documents_api
    r = client.post("/documents", json={"offer_id": offer_id, "kind": "affiche"})
    assert r.status_code == 422


def test_get_document_exposes_fields_and_checks(documents_api, subtests):
    client, _ = documents_api
    body = client.get(f"/documents/{DOC_ID}").json()

    with subtests.test("contrat de champs"):
        assert set(body["fields"][0]) == {
            "id",
            "text",
            "original",
            "edited",
            "page",
            "left",
            "top",
            "width",
            "height",
        }
    with subtests.test("verdict des contrôles présent"):
        assert body["checks"]["pages"] == 1
        assert body["checks"]["ok"] is True

    with subtests.test("aucun chemin disque ne fuite"):
        # Le service manipule des chemins ; le contrat HTTP n'expose que des URL.
        assert body["pdf_url"] == f"/documents/{DOC_ID}/pdf"
        assert body["preview_urls"] == [f"/documents/{DOC_ID}/preview/1.png"]
        assert "pdf_path" not in body and "preview_paths" not in body


def test_unknown_document_is_404(documents_api):
    client, _ = documents_api
    assert client.get("/documents/00000000cafe0000").status_code == 404


# --- Artefacts servis -------------------------------------------------------


def test_preview_and_pdf_are_served(documents_api, subtests):
    client, _ = documents_api
    body = client.get(f"/documents/{DOC_ID}").json()

    with subtests.test("aperçu PNG"):
        r = client.get(body["preview_urls"][0])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    with subtests.test("PDF"):
        r = client.get(body["pdf_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize("doc_id", ["../../data", "..%2F..%2Fdata", "pas-un-id"])
def test_malformed_doc_id_never_serves_a_file(documents_api, doc_id):
    """Un id d'URL sert à construire un chemin : il ne doit jamais sortir du dossier."""
    client, _ = documents_api
    assert client.get(f"/documents/{doc_id}/pdf").status_code == 404


# --- Édition ----------------------------------------------------------------


def test_patch_recompiles_and_rechecks(documents_api, subtests):
    """PATCH applique la retouche, RECOMPILE, et renvoie un verdict remesuré."""
    client, _ = documents_api
    before = client.get(f"/documents/{DOC_ID}").json()
    target = next(f for f in before["fields"] if "." in f["id"])

    r = client.patch(
        f"/documents/{DOC_ID}/fields",
        json={"edits": {target["id"]: "Puce réécrite à la main."}},
    )
    assert r.status_code == 200
    body = r.json()
    edited = next(f for f in body["fields"] if f["id"] == target["id"])

    with subtests.test("le texte rendu est celui saisi"):
        assert edited["text"] == "Puce réécrite à la main."
    with subtests.test("la zone est marquée retouchée"):
        assert edited["edited"] is True
        assert body["edited_field_ids"] == [target["id"]]
    with subtests.test("le texte généré reste accessible"):
        assert edited["original"] == target["text"]
    with subtests.test("la boîte a été remesurée"):
        assert edited["width"] != target["width"]


def test_patch_overflowing_reports_the_page_violation(documents_api):
    """Une retouche qui fait déborder est SIGNALÉE (jamais tronquée), aperçus compris."""
    client, _ = documents_api
    target = next(
        f for f in client.get(f"/documents/{DOC_ID}").json()["fields"] if "." in f["id"]
    )

    body = client.patch(
        f"/documents/{DOC_ID}/fields",
        json={"edits": {target["id"]: "Débordement volontaire. " * 200}},
    ).json()

    assert body["checks"]["ok"] is False
    assert any("attendu 1" in v for v in body["checks"]["page_violations"])
    assert body["page_count"] > 1
    assert len(body["preview_urls"]) == body["page_count"]


def test_reset_restores_the_generated_text(documents_api):
    client, _ = documents_api
    target = next(
        f for f in client.get(f"/documents/{DOC_ID}").json()["fields"] if "." in f["id"]
    )
    client.patch(
        f"/documents/{DOC_ID}/fields", json={"edits": {target["id"]: "Provisoire."}}
    )

    body = client.post(f"/documents/{DOC_ID}/reset", json={"field_ids": []}).json()
    assert body["edited_field_ids"] == []
    assert (
        next(f for f in body["fields"] if f["id"] == target["id"])["text"]
        == target["text"]
    )


def test_patch_on_unknown_document_is_404(documents_api):
    client, _ = documents_api
    r = client.patch("/documents/00000000cafe0000/fields", json={"edits": {"x": "y"}})
    assert r.status_code == 404


# --- Choix du template de CV ------------------------------------------------


def test_templates_route_lists_the_offered_layouts(documents_api):
    client, _ = documents_api
    r = client.get("/documents/templates")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "compact"
    # « classic » n'est plus proposé à l'écran (il reste rendu pour les anciens documents).
    assert [(t["name"], t["label"]) for t in body["templates"]] == [
        ("compact", "Classique"),
        ("basic", "Stylé"),
        ("modern_cv", "Contrasté"),
        ("cobalt", "Cobalt"),
        ("lavandula", "Lavande"),
        ("metronic", "Metronic"),
    ]
    basic = body["templates"][1]
    assert {o["name"]: o["default"] for o in basic["options"]} == {
        "couleur": "bleu",
        "accroche": True,
    }
    couleur = next(o for o in basic["options"] if o["name"] == "couleur")
    assert couleur["kind"] == "choice" and couleur["affects_budget"] is False
    assert [c["value"] for c in couleur["choices"]] == ["bleu", "noir-blanc"]


def test_templates_route_announces_the_beta_layouts(documents_api):
    """Le caractère « bêta » voyage par l'API, pour que le front n'ait aucune liste.

    Contrôle de la MÉCANIQUE, pas d'une liste de noms : chaque mise en page proposée
    porte la clé, et les trois mises en page historiques ne sont pas bêta.
    """
    client, _ = documents_api
    templates = client.get("/documents/templates").json()["templates"]
    assert all("beta" in t for t in templates), "la clé est toujours renvoyée"
    beta_par_nom = {t["name"]: t["beta"] for t in templates}
    for name in ("compact", "basic"):
        assert beta_par_nom[name] is False, f"{name} n'est pas une nouveauté"
    for name in ("modern_cv", "cobalt", "lavandula", "metronic"):
        assert beta_par_nom[name] is True, (
            f"{name} : mise en page récente, annoncée bêta"
        )
    # La lettre n'a pas la notion : sa spec n'a pas le champ, le défaut doit tenir.
    lettres = client.get("/documents/templates", params={"kind": "letter"}).json()
    assert all(t["beta"] is False for t in lettres["templates"])


def test_open_unknown_template_is_422_before_any_generation(documents_api, monkeypatch):
    """Un nom invalide est refusé à la validation : le modèle n'est jamais appelé."""

    def _no_generation(*_args, **_kwargs):
        raise AssertionError("aucune génération ne doit être tentée")

    monkeypatch.setattr(DocumentEditService, "_generate_draft", _no_generation)
    client, offer_id = documents_api
    r = client.post(
        "/documents",
        json={
            "offer_id": offer_id,
            "kind": "cv",
            "template": "basic_eng",
            "force_new": True,
        },
    )
    assert r.status_code == 422
    assert "basic_eng" in r.text


def test_open_with_the_draft_template_resumes_it(documents_api):
    client, offer_id = documents_api
    r = client.post(
        "/documents", json={"offer_id": offer_id, "kind": "cv", "template": "compact"}
    )
    assert r.status_code == 200
    assert r.json()["doc_id"] == DOC_ID


def test_open_with_another_template_generates_instead_of_resuming(
    documents_api, monkeypatch
):
    """Choisir « basic » pour une offre qui n'a qu'un brouillon « compact » : on génère."""
    requested: list[str] = []

    def _fake_generation(_self, _offer_id, _description, _kind, template):
        requested.append(template)
        raise DocumentEditError("génération simulée")

    monkeypatch.setattr(DocumentEditService, "_generate_draft", _fake_generation)
    client, offer_id = documents_api
    r = client.post(
        "/documents", json={"offer_id": offer_id, "kind": "cv", "template": "basic"}
    )
    assert r.status_code == 502, "le brouillon compact n'a pas été repris"
    assert requested == ["basic"]


def _record_generation(monkeypatch) -> list[str]:
    """Remplace la génération par un enregistreur de la référence demandée (502 simulé)."""
    requested: list[str] = []

    def _fake_generation(_self, _offer_id, _description, _kind, template):
        requested.append(template)
        raise DocumentEditError("génération simulée")

    monkeypatch.setattr(DocumentEditService, "_generate_draft", _fake_generation)
    return requested


@pytest.mark.parametrize(
    "options",
    [{"couleur": "rouge"}, {"police": "Arial"}, {"accroche": "oui"}],
)
def test_invalid_options_are_422_before_any_generation(
    documents_api, monkeypatch, options
):
    requested = _record_generation(monkeypatch)
    client, offer_id = documents_api
    r = client.post(
        "/documents",
        json={
            "offer_id": offer_id,
            "kind": "cv",
            "template": "basic",
            "options": options,
            "force_new": True,
        },
    )
    assert r.status_code == 422
    assert requested == []


def test_options_are_part_of_the_reuse_key(documents_api, monkeypatch):
    """Brouillon « compact » existant ; « compact + soft skills » est un AUTRE document."""
    requested = _record_generation(monkeypatch)
    client, offer_id = documents_api
    r = client.post(
        "/documents",
        json={
            "offer_id": offer_id,
            "kind": "cv",
            "template": "compact",
            "options": {"soft_skills": True},
        },
    )
    assert r.status_code == 502
    assert requested == ["compact?soft_skills=true"], "référence canonique transmise"


def test_legacy_named_draft_is_reused_for_the_equivalent_options(
    documents_api, typst_out_dir, monkeypatch
):
    """Un document enregistré sous « compact-soft » est repris pour compact + soft skills."""
    requested = _record_generation(monkeypatch)
    client, offer_id = documents_api

    conn = store.connect(typst_out_dir / "drafts.db")
    try:
        legacy = store.get_draft(conn, LOCAL_USER_ID, DOC_ID, profile_key="")
        assert legacy is not None
        store.save_draft(
            conn,
            LOCAL_USER_ID,
            legacy.model_copy(
                update={
                    "doc_id": "fedcba9876543210",
                    "template": "compact-soft",
                    "updated_at": store.now_iso(),
                }
            ),
            profile_key="",
        )
    finally:
        conn.close()

    r = client.post(
        "/documents",
        json={
            "offer_id": offer_id,
            "kind": "cv",
            "template": "compact",
            "options": {"soft_skills": True},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert requested == []
    assert body["doc_id"] == "fedcba9876543210"
    assert body["template"] == "compact"
    assert body["options"] == {"accroche": False, "soft_skills": True}


def test_view_exposes_base_template_and_resolved_options(documents_api):
    client, _ = documents_api
    body = client.get(f"/documents/{DOC_ID}").json()
    assert body["template"] == "compact"
    assert body["options"] == {"accroche": False, "soft_skills": False}
