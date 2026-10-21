"""La consommation du modèle est réellement ENREGISTRÉE, et réellement comptée.

`core/db/schema.py` affirmait depuis la création de la table : « Écrite dès maintenant, pas
“plus tard” : chaque génération coûte de l'argent réel, et une consommation non enregistrée
ne se rattrape jamais rétroactivement. » La table, son index, la colonne d'administration et
la tuile du front existaient — et aucune ligne n'y était jamais écrite.

Ces tests portent donc sur les deux moitiés du chemin, séparément :
  1. l'écriture elle-même arrive en base, et l'administration la voit ;
  2. les services de génération APPELLENT bien leur enregistreur.

Vérifier l'un sans l'autre laisserait passer exactement le défaut d'origine : une table
correcte que personne n'alimente.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from jobtool.core.db import connect_sqlite, ensure_user, llm_usage, make_engine
from jobtool.services.admin_service import AdminService
from jobtool.services.usage_service import record_llm_call, recorder_for

#: Même convention que les autres tests : chemin relatif à la racine du dépôt, d'où la
#: suite est toujours lancée.
BASE_EXAMPLE_YAML = Path("data/base.example.yaml")

ALICE = uuid.UUID("33333333-3333-3333-3333-333333333333")
BOB = uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture
def db(tmp_path):
    """Base jetable avec deux comptes."""
    chemin = tmp_path / "usage.db"
    conn = connect_sqlite(chemin)
    ensure_user(conn, ALICE, email="alice@exemple.fr", name="Alice")
    ensure_user(conn, BOB, email="bob@exemple.fr", name="Bob")
    conn.commit()
    conn.close()
    return chemin


def _rows(db):
    conn = connect_sqlite(db)
    try:
        return list(conn.execute(select(llm_usage)))
    finally:
        conn.close()


# --- 1. L'écriture arrive en base -------------------------------------------


def test_un_appel_ecrit_une_ligne(db):
    record_llm_call(db, ALICE, kind="cv", model="mistral-small-latest")
    (ligne,) = _rows(db)
    assert ligne.user_id == ALICE
    assert ligne.kind == "cv" and ligne.model == "mistral-small-latest"
    assert ligne.created_at, "l'horodatage est renseigné"
    assert ligne.prompt_tokens == 0 and ligne.completion_tokens == 0, (
        "jetons à zéro tant que le client ne les remonte pas — le NOMBRE d'appels compte"
    )


def test_les_appels_sont_imputes_au_bon_compte(db):
    record_llm_call(db, ALICE, kind="cv", model="m")
    record_llm_call(db, BOB, kind="letter", model="m")
    record_llm_call(db, BOB, kind="cv", model="m")

    conn = connect_sqlite(db)
    try:
        par_compte = {
            row[0]: row[1]
            for row in conn.execute(
                select(llm_usage.c.user_id, func.count()).group_by(llm_usage.c.user_id)
            )
        }
    finally:
        conn.close()
    assert par_compte == {ALICE: 1, BOB: 2}


def test_l_administration_voit_la_consommation(db):
    """Le bout visible : la colonne « Appels LLM » cesse d'afficher zéro."""
    record_llm_call(db, ALICE, kind="cv", model="m")
    record_llm_call(db, ALICE, kind="letter", model="m")
    record_llm_call(db, BOB, kind="cv", model="m")

    apercu = AdminService(make_engine(f"sqlite:///{db}")).overview()
    par_email = {a.email: a for a in apercu.accounts}
    assert par_email["alice@exemple.fr"].llm_calls == 2
    assert par_email["bob@exemple.fr"].llm_calls == 1
    assert apercu.total_llm_calls == 3


def test_un_echec_d_ecriture_ne_leve_jamais(tmp_path):
    """Une comptabilité en panne ne doit pas faire échouer une génération déjà payée.

    Ici le compte n'existe pas : la clé étrangère refuse la ligne. L'appel doit se taire.
    """
    chemin = tmp_path / "vide.db"
    connect_sqlite(chemin).close()
    record_llm_call(chemin, uuid.uuid4(), kind="cv", model="m")  # ne lève pas
    assert _rows(chemin) == []


# --- 2. Les services appellent bien leur enregistreur ------------------------


def test_le_generateur_note_chaque_generation(tmp_path, monkeypatch):
    """`Generator` appelle son enregistreur — c'est le câblage qui manquait.

    Le modèle n'est jamais sollicité : on remplace l'étape de génération. Ce qu'on éprouve
    est le point d'appel, pas la génération elle-même.
    """
    from jobtool.api.generation import Generator
    from jobtool.core.experience_base.store import YamlExperienceBase
    from jobtool.services import batch_service

    notes: list[tuple[str, str]] = []

    class _Resultat:
        rendered = True
        pdf_path = None
        error = "peu importe : on n'éprouve que l'enregistrement"

    monkeypatch.setattr(
        batch_service, "process_generate", lambda *a, **k: _Resultat(), raising=True
    )
    monkeypatch.setattr(
        batch_service, "process_letter", lambda *a, **k: _Resultat(), raising=True
    )

    gen = Generator(
        YamlExperienceBase(BASE_EXAMPLE_YAML),
        output_dir=tmp_path / "out",
        record_usage=lambda kind, model: notes.append((kind, model)),
    )
    offre = _offre()
    gen.generate_cv(offre)
    gen.generate_letter(offre)

    assert [kind for kind, _ in notes] == ["cv", "letter"]
    assert all(model for _, model in notes), "le modèle utilisé est renseigné"


def test_rien_n_est_note_si_le_modele_n_a_pas_ete_appele(tmp_path):
    """Base d'expériences illisible : on échoue AVANT le modèle, donc rien à facturer."""
    from jobtool.api.generation import Generator
    from jobtool.core.experience_base.store import YamlExperienceBase

    notes: list[tuple[str, str]] = []
    gen = Generator(
        YamlExperienceBase(tmp_path / "absente.yaml"),
        output_dir=tmp_path / "out",
        record_usage=lambda kind, model: notes.append((kind, model)),
    )
    resultat = gen.generate_cv(_offre())
    assert not resultat.ok
    assert notes == [], "un appel qui n'a pas eu lieu n'est pas compté"


def test_l_enregistreur_cable_ecrit_vraiment(db):
    """`recorder_for` relie les deux moitiés : appelé, il produit bien la ligne."""
    record = recorder_for(db, ALICE)
    record("cv", "mistral-small-latest")
    (ligne,) = _rows(db)
    assert (ligne.user_id, ligne.kind) == (ALICE, "cv")


def _offre():
    from jobtool.aggregator.models import Job

    return Job(
        id="h1",
        source="manual",
        native_id="",
        title="t",
        company="c",
        location="l",
        description="Une offre.",
        fetched_date="2026-01-01T00:00:00+00:00",
        dedup_hash="h1",
    )
