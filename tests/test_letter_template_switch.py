"""Choix et changement du template d'une lettre — et mode pro sur l'en-tête — SANS LLM.

Ce qui est prouvé, avec Typst et poppler réels :

  1. Changer de template RECOMPILE le même brouillon : aucun appel à la génération, texte,
     retouches de zones et source du mode pro conservés ; rien n'est enregistré sur un nom
     inconnu. Réservé à la lettre.
  2. Ouvrir une lettre en demandant un template reprend le brouillon existant et le bascule ;
     sans brouillon, la génération reçoit ce template et l'enregistre.
  3. Rétrocompatibilité : `""` et `classic` désignent « sobre » ; un brouillon enregistré
     avant titre / destinataire / salutation se recompile dans les deux templates.
  4. Mode pro : nom, entreprise et date réécrits sont ceux que le contrôle ATS cherche ;
     les avertissements de mise en page restent jugés.
  5. API : 422 sur un nom inconnu (ouverture comme changement), liste des templates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_document_edit_service, get_offers_service
from jobtool.api.main import app
from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.core.rendering.pdf_checks import extract_pdf_text
from jobtool.documents.cover_letter.render import build_render_input
from jobtool.documents.cover_letter.schema import CoverLetterRender
from jobtool.documents.cv.profiles import get_profile
from jobtool.documents.cv.render import build_render_input as build_cv_input
from jobtool.documents.drafts import store
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.services import document_edit_service as edit_module
from jobtool.services.document_edit_service import (
    DocumentEditService,
    DocumentTemplateError,
)
from jobtool.services.offers_service import OffersService

BASE_YAML = Path("data/base.example.yaml")
LETTER_ID = "1e77e40000000001"
CV_ID = "1e77e40000000002"
OFFER_ID = "offre-lettre"


def _letter_input() -> dict:
    return build_render_input(
        CoverLetterRender(
            nom="Lucas Moreau",
            coordonnees=["lucas.moreau@example.com", "+33 6 12 34 56 78"],
            date="12 juin 2026",
            lieu="Lyon",
            entreprise="FinUp Technologies",
            paragraphe_vous="Votre équipe construit des services de paiement fiables.",
            paragraphe_moi="J'ai conçu et testé des API lors de mes stages.",
            paragraphe_nous="Je serais heureux d'en discuter lors d'un entretien.",
            formule="Je vous prie d'agréer mes salutations distinguées.",
        )
    )


def _no_llm(*_args, **_kwargs):
    raise AssertionError("le modèle ne doit pas être rappelé")


@pytest.fixture
def service(typst_out_dir, monkeypatch):
    """Service dont la génération est interdite : tout rappel au modèle fait échouer."""
    monkeypatch.setattr(DocumentEditService, "_generate_letter_input", _no_llm)
    monkeypatch.setattr(DocumentEditService, "_generate_cv_input", _no_llm)
    return DocumentEditService(
        base_repo=YamlExperienceBase(BASE_YAML),
        drafts_db=typst_out_dir / "drafts.db",
        work_root=typst_out_dir / "docs",
        profile_key="",
    )


def _seed(service, doc_id, kind, template, data, overrides=None):
    now = store.now_iso()
    draft = DocumentDraft(
        doc_id=doc_id,
        offer_id=OFFER_ID,
        kind=kind,
        template=template,
        render_input=data,
        overrides=overrides or {},
        created_at=now,
        updated_at=now,
    )
    originals = service._compile(draft, data, service._work_dir(doc_id) / "_original")
    draft = draft.model_copy(
        update={"original_texts": {b.id: b.text for b in originals.fields}}
    )
    conn = store.connect(service._drafts_db)
    try:
        store.save_draft(conn, LOCAL_USER_ID, draft, profile_key="")
    finally:
        conn.close()


def _stored(service, doc_id) -> DocumentDraft:
    conn = store.connect(service._drafts_db)
    try:
        draft = store.get_draft(conn, LOCAL_USER_ID, doc_id, profile_key="")
    finally:
        conn.close()
    assert draft is not None
    return draft


def _pdf_text(service, doc_id) -> str:
    return " ".join(extract_pdf_text(str(service.pdf_path(doc_id))).split())


@pytest.fixture
def letter_doc(service):
    """Lettre d'avant le registre (template « »), avec une retouche de zone."""
    _seed(
        service,
        LETTER_ID,
        "letter",
        "",
        _letter_input(),
        overrides={"paragraphe_nous": "Paragraphe retouché à la main."},
    )
    return LETTER_ID


# --- 1. changer de template ------------------------------------------------------------


def test_switch_recompiles_the_same_draft_with_edits_kept(service, letter_doc):
    view = service.set_template(letter_doc, "moderne")

    assert view is not None
    assert view.doc_id == letter_doc and view.template == "moderne"
    assert view.edited_field_ids == ["paragraphe_nous"]
    assert view.page_count == 1 and view.checks.ok, view.checks
    assert "Paragraphe retouché à la main." in _pdf_text(service, letter_doc)
    assert "Service Recrutement" in _pdf_text(service, letter_doc)  # bloc « moderne »
    stored = _stored(service, letter_doc)
    assert stored.template == "moderne"
    assert stored.overrides == {"paragraphe_nous": "Paragraphe retouché à la main."}

    # Et retour : « sobre » n'affiche pas de bloc destinataire.
    assert service.set_template(letter_doc, "sobre").template == "sobre"
    assert "Service Recrutement" not in _pdf_text(service, letter_doc)


def test_switch_keeps_the_pro_mode_source(service, letter_doc):
    source = json.loads(service.get_source(letter_doc)[0])
    source["paragraphe_moi"] = "Texte réécrit en mode pro."
    service.set_source(letter_doc, json.dumps(source))

    view = service.set_template(letter_doc, "moderne")

    assert view.source_edited
    assert "Texte réécrit en mode pro." in _pdf_text(service, letter_doc)


def test_unknown_template_is_refused_and_nothing_is_stored(service, letter_doc):
    with pytest.raises(DocumentTemplateError, match="inconnu"):
        service.set_template(letter_doc, "../cv/cv_basic")
    assert _stored(service, letter_doc).template == ""


def test_cv_template_cannot_be_switched_without_regenerating(
    service, example_base, example_cv
):
    _seed(
        service,
        CV_ID,
        "cv",
        "compact",
        build_cv_input(example_base, example_cv, get_profile("compact")),
    )
    with pytest.raises(DocumentTemplateError, match="lettre"):
        service.set_template(CV_ID, "moderne")
    assert _stored(service, CV_ID).template == "compact"


def test_unknown_document_is_none(service):
    assert service.set_template("0000000000000000", "moderne") is None


# --- 2. ouverture avec un template -------------------------------------------------------


def test_open_with_a_template_switches_the_existing_draft(service, letter_doc):
    view = service.open_document(OFFER_ID, "offre", "letter", "moderne")

    assert view.doc_id == letter_doc and view.template == "moderne"
    assert _stored(service, letter_doc).template == "moderne"


def test_open_without_a_template_resumes_as_is(service, letter_doc):
    view = service.open_document(OFFER_ID, "offre", "letter")
    assert view.doc_id == letter_doc and view.template == "sobre"
    assert _stored(service, letter_doc).template == ""


def test_generation_receives_and_stores_the_chosen_template(service, monkeypatch):
    monkeypatch.setattr(
        DocumentEditService,
        "_generate_letter_input",
        lambda _self, _description, _out: _letter_input(),
    )
    view = service.open_document("offre-neuve", "offre", "letter", "moderne")

    assert view.template == "moderne"
    assert _stored(service, view.doc_id).template == "moderne"
    assert "Service Recrutement" in _pdf_text(service, view.doc_id)


# --- 3. rétrocompatibilité ----------------------------------------------------------------


@pytest.mark.parametrize("legacy", ["", "classic"])
def test_legacy_values_render_as_sobre(service, legacy):
    _seed(service, LETTER_ID, "letter", legacy, _letter_input())
    view = service.get_document(LETTER_ID)
    assert view.template == "sobre" and view.checks.ok


@pytest.mark.parametrize("template", ["sobre", "moderne"])
def test_draft_saved_before_the_new_fields_still_compiles(service, template):
    old = {
        k: v
        for k, v in _letter_input().items()
        if k not in ("titre", "destinataire", "salutation")
    }
    _seed(service, LETTER_ID, "letter", template, old)

    view = service.get_document(LETTER_ID)

    assert view.checks.ok, view.checks
    assert "Madame, Monsieur," in _pdf_text(service, LETTER_ID)
    assert json.loads(service.get_source(LETTER_ID)[0])["salutation"] == (
        "Madame, Monsieur,"
    )


# --- 4. mode pro : en-tête réécrit ----------------------------------------------------------


@pytest.mark.parametrize("template", ["sobre", "moderne"])
def test_ats_checks_the_rewritten_header(service, letter_doc, template, monkeypatch):
    service.set_template(letter_doc, template)
    source = json.loads(service.get_source(letter_doc)[0])
    source.update(
        nom="Camille Laurent", entreprise="L'Atelier Nouveau", date="3 mars 2027"
    )

    targets: list[list[str]] = []
    real_check = edit_module.check_pdf_ats

    def spy(pdf_path, expected):
        targets.append(list(expected))
        return real_check(pdf_path, expected)

    monkeypatch.setattr(edit_module, "check_pdf_ats", spy)
    view = service.set_source(letter_doc, json.dumps(source))

    assert targets[-1] == ["Camille Laurent", "L'Atelier Nouveau", "3 mars 2027"]
    assert view.checks.missing_ats == []
    text = _pdf_text(service, letter_doc)
    assert "Lucas Moreau" not in text and "FinUp" not in text and "12 juin" not in text


def test_header_fields_and_salutation_are_editable_in_pro_mode(service, letter_doc):
    service.set_template(letter_doc, "moderne")
    source = json.loads(service.get_source(letter_doc)[0])
    source.update(
        titre="Ingénieur logiciel",
        destinataire="Claire Martin",
        salutation="Madame,",
        lieu="Nancy",
        coordonnees=["camille@example.com"],
    )
    service.set_source(letter_doc, json.dumps(source))

    text = _pdf_text(service, letter_doc)
    for expected in ("Ingénieur logiciel", "Claire Martin", "Madame,", "Nancy"):
        assert expected in text
    assert "Service Recrutement" not in text and "Lyon" not in text


def test_layout_warnings_are_reported_in_pro_mode(service, letter_doc):
    service.set_template(letter_doc, "moderne")
    source = json.loads(service.get_source(letter_doc)[0])
    source["coordonnees"] = ["a@example.com", "b.dev", "c.dev", "d.dev", "e.dev"]

    view = service.set_source(letter_doc, json.dumps(source))

    assert view.checks.layout_warnings and not view.checks.ok
    assert "e.dev" in _pdf_text(service, letter_doc)  # averti, pas coupé


# --- 5. API ---------------------------------------------------------------------------------


@pytest.fixture
def client(service, letter_doc, seed_offers, tmp_db):
    offer_id = seed_offers(tmp_db)[0].dedup_hash
    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    app.dependency_overrides[get_document_edit_service] = lambda: service
    yield TestClient(app), offer_id
    app.dependency_overrides.clear()


def test_api_switch_template(client):
    http, _ = client
    r = http.put(f"/documents/{LETTER_ID}/template", json={"template": "moderne"})
    assert r.status_code == 200
    assert r.json()["template"] == "moderne"
    assert r.json()["checks"]["layout_warnings"] == []


@pytest.mark.parametrize("name", ["inconnu", "lettre_moderne.typ", ""])
def test_api_unknown_template_is_422(client, name):
    http, _ = client
    r = http.put(f"/documents/{LETTER_ID}/template", json={"template": name})
    if name == "":
        # « » est l'ancien nom du template historique : accepté.
        assert r.status_code == 200 and r.json()["template"] == "sobre"
    else:
        assert r.status_code == 422


def test_api_switch_unknown_document_is_404(client):
    http, _ = client
    r = http.put("/documents/0000000000000000/template", json={"template": "sobre"})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "letter", "template": "inconnu"},
        {"kind": "letter", "template": "moderne", "options": {"couleur": "bleu"}},
    ],
)
def test_api_open_letter_with_an_invalid_choice_is_422(client, body):
    http, offer_id = client
    r = http.post("/documents", json={"offer_id": offer_id, **body})
    assert r.status_code == 422


def test_api_lists_letter_templates(client):
    http, _ = client
    body = http.get("/documents/templates", params={"kind": "letter"}).json()

    assert body["default"] == "sobre"
    assert [t["name"] for t in body["templates"]] == ["sobre", "moderne"]
    assert [t["label"] for t in body["templates"]] == ["Sobre", "Moderne"]
    for template in body["templates"]:
        assert template["options"] == []
        assert template["previews"] == [
            {"options": {}, "url": f"assets/templates/lettre--{template['name']}.png"}
        ]
    assert (
        http.get("/documents/templates", params={"kind": "affiche"}).status_code == 422
    )
