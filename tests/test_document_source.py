"""Preuves du MODE PRO : éditer les données complètes d'un document et recompiler.

Le mode pro expose l'entrée de compilation entière (render_input) d'un CV — quel que soit
son template — ou d'une lettre. Ce qui est prouvé ici, sans LLM mais avec Typst réel :

  1. Lecture   — la source exposée est exactement celle de l'aperçu (retouches comprises).
  2. Écriture  — une modification (ex. une compétence ajoutée) se retrouve dans le PDF ;
                 une source partielle est complétée ; budget et traçabilité ne jugent plus.
  3. Refus     — JSON illisible, forme invalide ou erreur de compilation : 422, et RIEN
                 n'est enregistré (le document reste dans son dernier état valide).
  4. Retour    — revenir aux données générées rend les retouches de zones intactes ;
                 retoucher une zone pendant le mode pro est refusé (409).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from jobtool.api.deps import get_document_edit_service
from jobtool.api.main import app
from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.core.rendering.pdf_checks import extract_pdf_text
from jobtool.documents.cover_letter.render import (
    build_render_input as build_letter_input,
)
from jobtool.documents.cover_letter.schema import CoverLetterRender
from jobtool.documents.cover_letter.source import normalize_letter_source
from jobtool.documents.cv.profiles import get_profile
from jobtool.documents.cv.render import _template_options, build_render_input
from jobtool.documents.cv.source import normalize_cv_source
from jobtool.documents.drafts import store
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.services.document_edit_service import (
    DocumentEditService,
    DocumentSourceError,
    SourceModeActive,
)

BASE_YAML = Path("data/base.example.yaml")
CV_ID = "c0ffee0000000001"
LETTER_ID = "c0ffee0000000002"


@pytest.fixture
def service(typst_out_dir):
    return DocumentEditService(
        base_repo=YamlExperienceBase(BASE_YAML),
        drafts_db=typst_out_dir / "drafts.db",
        work_root=typst_out_dir / "docs",
        profile_key="",
    )


def _seed(service: DocumentEditService, doc_id: str, kind: str, template: str, data):
    """Enregistre un brouillon comme le ferait une génération (textes d'origine relevés)."""
    now = store.now_iso()
    draft = DocumentDraft(
        doc_id=doc_id,
        offer_id=f"offre-{kind}",
        kind=kind,  # type: ignore[arg-type]
        template=template,
        render_input=data,
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


@pytest.fixture
def cv_doc(service, example_base, example_cv):
    data = build_render_input(example_base, example_cv, get_profile("compact"))
    _seed(service, CV_ID, "cv", "compact", data)
    return CV_ID


def _letter_render() -> CoverLetterRender:
    return CoverLetterRender(
        nom="Lucas Moreau",
        coordonnees=["lucas.moreau@example.com", "Lyon, France"],
        date="12 juin 2026",
        lieu="Lyon",
        entreprise="FinUp Technologies",
        paragraphe_vous="Votre équipe construit des services de paiement fiables.",
        paragraphe_moi="J'ai conçu et testé des API lors de mes stages.",
        paragraphe_nous="Je serais heureux d'en discuter lors d'un entretien.",
        formule="Je vous prie d'agréer mes salutations distinguées.",
    )


@pytest.fixture
def letter_doc(service):
    _seed(service, LETTER_ID, "letter", "", build_letter_input(_letter_render()))
    return LETTER_ID


def _source(service, doc_id) -> dict:
    found = service.get_source(doc_id)
    assert found is not None
    return json.loads(found[0])


def _stored(service, doc_id) -> DocumentDraft:
    conn = store.connect(service._drafts_db)
    try:
        draft = store.get_draft(conn, LOCAL_USER_ID, doc_id, profile_key="")
    finally:
        conn.close()
    assert draft is not None
    return draft


# --- 1. forme des sources : les rendus réels passent tels quels -----------------


@pytest.mark.parametrize(
    "ref", ["classic", "compact", "compact-soft-ctx", "basic?couleur=noir-blanc"]
)
def test_real_cv_render_inputs_are_valid_sources(example_base, example_cv, ref):
    base = example_base.model_copy(
        update={
            "profile": example_base.profile.model_copy(update={"accroche": "Accroche."})
        }
    )
    data = json.loads(
        json.dumps(
            build_render_input(
                base, example_cv, get_profile(ref), _template_options(ref)
            )
        )
    )
    assert normalize_cv_source(data) == data


def test_real_letter_render_input_is_a_valid_source():
    data = build_letter_input(_letter_render())
    assert normalize_letter_source(data) == data


# --- 2. lecture ---------------------------------------------------------------------


def test_source_is_the_current_compilation_input(service, cv_doc):
    first_bullet = _source(service, cv_doc)["experiences"][0]["bullets"][0]
    service.set_fields(cv_doc, {first_bullet["id"]: "Puce retouchée à la main."})

    source = _source(service, cv_doc)
    assert source["experiences"][0]["bullets"][0]["text"] == "Puce retouchée à la main."
    assert service.get_source(cv_doc)[1] is False, "pas encore en mode pro"


def test_unknown_document_has_no_source(service):
    assert service.get_source("0000000000000000") is None


# --- 3. écriture ----------------------------------------------------------------------


def test_adding_a_skill_recompiles_the_cv(service, cv_doc):
    source = _source(service, cv_doc)
    source["hard_skills"] = ["Rust", *source["hard_skills"]]

    view = service.set_source(cv_doc, json.dumps(source))

    assert view is not None and view.source_edited
    assert view.page_count == 1
    assert "Rust" in extract_pdf_text(view.pdf_path)
    assert _stored(service, cv_doc).edited_input["hard_skills"][0] == "Rust"
    # Le brouillon d'origine et les retouches ne sont pas touchés.
    assert "Rust" not in _stored(service, cv_doc).render_input["hard_skills"]


def test_content_bounds_are_not_judged_in_pro_mode(service, cv_doc):
    """Des puces qui dépasseraient le budget ne sont plus des « dépassements »."""
    source = _source(service, cv_doc)
    source["experiences"][0]["bullets"][0]["text"] = "Texte bien plus long. " * 7
    source["experiences"][0]["bullets"][0]["id"] = "id-libre-inconnu"

    view = service.set_source(cv_doc, json.dumps(source))

    assert view.checks.overruns == [] and view.checks.violations == []


def test_partial_source_is_completed(service, cv_doc):
    view = service.set_source(cv_doc, json.dumps({"hard_skills": ["Rust", "Go"]}))

    assert view.page_count == 1
    stored = _stored(service, cv_doc).edited_input
    assert stored["hard_skills"] == ["Rust", "Go"]
    assert stored["experiences"] == [] and stored["profile"]["links"] == []


def test_letter_paragraph_can_be_rewritten(service, letter_doc):
    source = _source(service, letter_doc)
    source["paragraphe_nous"] = "Disponible dès septembre pour un premier échange."

    view = service.set_source(letter_doc, json.dumps(source))

    assert view.source_edited and view.page_count == 1
    assert "Disponible dès septembre" in extract_pdf_text(view.pdf_path)


def test_basic_template_options_are_editable_in_the_source(
    service, example_base, example_cv
):
    base = example_base.model_copy(
        update={
            "profile": example_base.profile.model_copy(update={"accroche": "Accroche."})
        }
    )
    ref = "basic"
    data = build_render_input(
        base, example_cv, get_profile(ref), _template_options(ref)
    )
    _seed(service, "c0ffee0000000003", "cv", ref, data)

    source = _source(service, "c0ffee0000000003")
    source["options"]["couleur"] = "noir-blanc"
    source["accroche"] = "Nouvelle accroche écrite en mode pro."
    view = service.set_source("c0ffee0000000003", json.dumps(source))

    assert "Nouvelle accroche écrite en mode pro." in extract_pdf_text(view.pdf_path)


# --- 4. refus : rien n'est enregistré ---------------------------------------------


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('{"hard_skills": [', "JSON invalide"),
        ("[1, 2]", "objet JSON"),
        ('{"hard_skills": "Rust"}', "hard_skills"),
        ('{"experiences": [{"type": "stage"}]}', "experiences.0.type"),
    ],
)
def test_invalid_source_is_refused_and_not_stored(service, cv_doc, source, message):
    with pytest.raises(DocumentSourceError, match=message):
        service.set_source(cv_doc, source)
    assert _stored(service, cv_doc).edited_input is None


def test_source_that_does_not_compile_is_not_stored(service, example_base, example_cv):
    ref = "basic?accroche=false"
    data = build_render_input(
        example_base, example_cv, get_profile(ref), _template_options(ref)
    )
    _seed(service, "c0ffee0000000004", "cv", ref, data)
    source = _source(service, "c0ffee0000000004")
    source["options"]["couleur"] = (
        "rouge"  # couleur inconnue du template -> Typst échoue
    )

    with pytest.raises(DocumentSourceError, match="ne compile pas"):
        service.set_source("c0ffee0000000004", json.dumps(source))
    assert _stored(service, "c0ffee0000000004").edited_input is None


# --- 5. retour et exclusivité avec les zones ------------------------------------------


def test_reset_restores_generated_data_and_zone_edits(service, cv_doc):
    bullet_id = _source(service, cv_doc)["experiences"][0]["bullets"][0]["id"]
    service.set_fields(cv_doc, {bullet_id: "Retouche de zone conservée."})
    service.set_source(cv_doc, json.dumps({"hard_skills": ["Rust"]}))

    view = service.reset_source(cv_doc)

    assert view is not None and not view.source_edited
    assert bullet_id in view.edited_field_ids
    assert "Retouche de zone conservée." in extract_pdf_text(view.pdf_path)


def test_zone_edits_are_refused_while_pro_mode_is_active(service, cv_doc):
    service.set_source(cv_doc, json.dumps(_source(service, cv_doc)))
    with pytest.raises(SourceModeActive):
        service.set_fields(cv_doc, {"hard_skills": "Rust"})
    with pytest.raises(SourceModeActive):
        service.reset_fields(cv_doc, [])


# --- 6. API ---------------------------------------------------------------------------


@pytest.fixture
def client(service, cv_doc):
    app.dependency_overrides[get_document_edit_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_source_round_trip(client):
    r = client.get(f"/documents/{CV_ID}/source")
    assert r.status_code == 200 and r.json()["source_edited"] is False
    source = json.loads(r.json()["source"])
    source["hard_skills"].append("Rust")

    r = client.put(f"/documents/{CV_ID}/source", json={"source": json.dumps(source)})
    assert r.status_code == 200
    assert r.json()["source_edited"] is True

    r = client.patch(f"/documents/{CV_ID}/fields", json={"edits": {"x": "y"}})
    assert r.status_code == 409, "zones refusées en mode pro"

    r = client.post(f"/documents/{CV_ID}/source/reset")
    assert r.status_code == 200 and r.json()["source_edited"] is False


def test_api_invalid_source_is_422(client):
    r = client.put(f"/documents/{CV_ID}/source", json={"source": "{pas du json"})
    assert r.status_code == 422
    assert "JSON invalide" in r.json()["detail"]


def test_api_unknown_document_is_404(client):
    assert client.get("/documents/0000000000000000/source").status_code == 404
    r = client.put("/documents/0000000000000000/source", json={"source": "{}"})
    assert r.status_code == 404


# --- 7. base SQLite locale créée avant le mode pro -------------------------------------


def test_existing_local_database_gains_the_new_column(tmp_path):
    """Une base locale antérieure (sans `edited_input`) est rattrapée à l'ouverture."""
    db = tmp_path / "ancienne.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE documents (doc_id TEXT PRIMARY KEY, user_id CHAR(32), "
                "offer_id TEXT, kind TEXT, template TEXT, render_input JSON, "
                "original_texts JSON, overrides JSON, created_at TEXT, updated_at TEXT)"
            )
        )
    engine.dispose()

    conn = store.connect(db)
    try:
        columns = {c["name"] for c in inspect(conn).get_columns("documents")}
    finally:
        conn.close()
    assert "edited_input" in columns
