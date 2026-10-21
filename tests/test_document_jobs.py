"""Génération en arrière-plan des documents d'une offre — SANS LLM.

Le registre est testé avec un exécuteur SYNCHRONE (la tâche tourne dans `submit`) ou
RETENU (la tâche attend qu'on la lance) : aucun thread, aucune attente dans les tests. Les
routes sont testées avec un vrai brouillon de CV (Typst réel) ; la lettre, qui demanderait
le modèle, échoue volontairement pour vérifier qu'un échec n'emporte pas l'autre document.
"""

from __future__ import annotations

import uuid
from concurrent.futures import Executor, Future
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import (
    get_document_edit_service,
    get_document_jobs,
    get_offers_service,
)
from jobtool.api.main import app
from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.documents.drafts import store
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.services.document_edit_service import (
    DocumentEditError,
    DocumentEditService,
)
from jobtool.services.document_jobs import DocumentJobs
from jobtool.services.offers_service import OffersService

ALICE = (uuid.UUID("11111111-1111-1111-1111-111111111111"), "")
BOB = (uuid.UUID("22222222-2222-2222-2222-222222222222"), "")


class InlineExecutor(Executor):
    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class HeldExecutor(Executor):
    """Garde les tâches soumises ; `run_all` les exécute."""

    def __init__(self):
        self.held = []

    def submit(self, fn, /, *args, **kwargs):
        self.held.append((fn, args, kwargs))
        return Future()

    def run_all(self):
        for fn, args, kwargs in self.held:
            fn(*args, **kwargs)
        self.held.clear()


# --- Registre ---------------------------------------------------------------------


def test_une_tache_enchaine_cv_puis_lettre():
    made = []
    jobs = DocumentJobs(InlineExecutor())

    job = jobs.start(
        ALICE, "offre", "Data engineer", lambda kind: made.append(kind) or f"id-{kind}"
    )

    assert made == ["cv", "letter"]
    assert job.status == "done"
    assert [(s.kind, s.status, s.doc_id) for s in job.steps] == [
        ("cv", "done", "id-cv"),
        ("letter", "done", "id-letter"),
    ]


def test_un_echec_n_emporte_pas_l_autre_document():
    def make(kind):
        if kind == "cv":
            raise RuntimeError("quota dépassé")
        return "id-lettre"

    job = DocumentJobs(InlineExecutor()).start(ALICE, "offre", "Titre", make)

    assert job.status == "partial"
    assert job.steps[0].error == "quota dépassé"
    assert job.steps[1].status == "done"


def test_un_double_clic_ne_lance_pas_deux_generations():
    executor = HeldExecutor()
    jobs = DocumentJobs(executor)

    first = jobs.start(ALICE, "offre", "Titre", lambda kind: kind)
    again = jobs.start(ALICE, "offre", "Titre", lambda kind: kind)

    assert again.id == first.id and len(executor.held) == 1
    assert jobs.get(ALICE, first.id).status == "running"
    executor.run_all()
    assert jobs.get(ALICE, first.id).status == "done"


def test_les_taches_sont_cloisonnees_par_compte():
    jobs = DocumentJobs(InlineExecutor())
    job = jobs.start(ALICE, "offre", "Titre", lambda kind: kind)

    assert [j.id for j in jobs.list(ALICE)] == [job.id]
    assert jobs.list(BOB) == []
    assert jobs.get(BOB, job.id) is None
    assert jobs.list((ALICE[0], "autre-profil")) == []


# --- Routes -----------------------------------------------------------------------

DOC_ID = "0123456789abcdef"


@pytest.fixture
def api(seed_offers, tmp_db, typst_out_dir, example_base, example_cv, monkeypatch):
    """(client, offer_id) : une offre avec un CV déjà prêt ; la lettre ne peut pas être
    générée (pas de modèle en test)."""
    from jobtool.documents.cv.profiles import get_profile
    from jobtool.documents.cv.render import build_render_input

    offer_id = seed_offers(tmp_db)[0].dedup_hash
    service = DocumentEditService(
        base_repo=YamlExperienceBase(Path("data/base.example.yaml")),
        drafts_db=typst_out_dir / "drafts.db",
        work_root=typst_out_dir / "docs",
        profile_key="",
    )
    render_input = build_render_input(example_base, example_cv, get_profile("compact"))
    now = store.now_iso()
    draft = DocumentDraft(
        doc_id=DOC_ID,
        offer_id=offer_id,
        kind="cv",
        template="compact",
        render_input=render_input,
        created_at=now,
        updated_at=now,
    )
    conn = store.connect(service._drafts_db)
    try:
        store.save_draft(conn, LOCAL_USER_ID, draft, profile_key="")
    finally:
        conn.close()

    def no_model(*_args, **_kwargs):
        raise DocumentEditError("aucun modèle en test")

    monkeypatch.setattr(service, "_generate_draft", no_model)
    jobs = DocumentJobs(InlineExecutor())
    app.dependency_overrides[get_offers_service] = lambda: OffersService(tmp_db)
    app.dependency_overrides[get_document_edit_service] = lambda: service
    app.dependency_overrides[get_document_jobs] = lambda: jobs
    yield TestClient(app), offer_id
    app.dependency_overrides.clear()


def test_route_lance_puis_liste_la_tache(api):
    client, offer_id = api

    started = client.post("/documents/jobs", json={"offer_id": offer_id})

    assert started.status_code == 202, started.text
    body = started.json()
    assert body["status"] == "partial", "CV rouvert, lettre en échec"
    cv, letter = body["steps"]
    assert (cv["kind"], cv["status"], cv["doc_id"]) == ("cv", "done", DOC_ID)
    assert letter["status"] == "error" and "aucun modèle" in letter["error"]
    assert [j["id"] for j in client.get("/documents/jobs").json()] == [body["id"]]


def test_route_liste_les_documents_prets_de_l_offre(api):
    client, offer_id = api

    docs = client.get(f"/documents/offer/{offer_id}").json()

    assert [(d["kind"], d["doc_id"], d["template"]) for d in docs] == [
        ("cv", DOC_ID, "compact")
    ]
    assert docs[0]["page_count"] >= 1
    preview = client.get(docs[0]["preview_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert client.get(docs[0]["pdf_url"]).status_code == 200


def test_route_offre_inconnue(api):
    client, _ = api
    assert (
        client.post("/documents/jobs", json={"offer_id": "inconnue"}).status_code == 404
    )
    assert client.get("/documents/offer/inconnue").json() == []
