"""Preuve du service profil + routes /profile — YAML temporaire, sans réseau ni LLM.

Couvre : projection base -> profil (read), écriture avec PRÉSERVATION des champs non édités
(education, interests, location, accroche, pinned), round-trip nom/langues, erreur de validation
(date invalide) -> ProfileError / 400, et les routes GET/PUT via TestClient + base injectée.
"""

from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_profile_service
from jobtool.api.main import app
from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.services.profile_service import ProfileError, ProfileService

_BASE_YAML = """
profile:
  name: Camille Martin
  email: camille@example.com
  phone: "0600000000"
  location: Paris, France
  links: [https://example.com/cm]
  accroche_template: "Étudiant spécialisé en {focus}."
education:
  - degree: Master Informatique
    school: Université X
    start: "2020-09"
    end: "2022-06"
languages:
  - name: Français
    level: langue maternelle
  - name: Anglais
    level: B2
hard_skills: [Python, SQL]
soft_skills: [Rigueur, Autonomie]
interests: [Échecs, Course à pied]
experiences:
  - id: exp-job
    type: job
    pinned: true
    title: Développeur
    organization: Acme
    start: "2022-01"
    end: present
    summary: A développé des choses.
    achievements:
      - id: exp-job.1
        text: A livré une fonctionnalité clé.
        skills: [python, sql]
  - id: exp-projet
    type: school_project
    title: Compilateur
    organization: Univ
    start: "2021-01"
    end: "2021-06"
    achievements:
      - id: exp-projet.1
        text: Analyse lexicale.
        skills: [lex]
"""


@pytest.fixture
def base_file(tmp_path):
    p = tmp_path / "moi.yaml"
    p.write_text(textwrap.dedent(_BASE_YAML), encoding="utf-8")
    return p


@pytest.fixture
def service(base_file):
    return ProfileService(YamlExperienceBase(base_file))


# --- Service : lecture ------------------------------------------------------


def test_read_projects_base_to_profile(service):
    dto = service.read()
    assert dto.prenom == "Camille" and dto.nom == "Martin", "name scindé prénom/nom"
    assert dto.telephone == "0600000000" and dto.email == "camille@example.com"
    assert dto.hard == ["Python", "SQL"] and dto.soft == ["Rigueur", "Autonomie"]
    assert dto.langues == ["Français — langue maternelle", "Anglais — B2"]
    exp = dto.experiences[0]
    assert exp.id == "exp-job" and exp.type == "job" and exp.titre == "Développeur"
    assert exp.org == "Acme" and exp.debut == "2022-01" and exp.fin == "present"
    assert exp.realisations[0].skills == ["python", "sql"]


# --- Service : écriture + préservation --------------------------------------


def test_write_persists_and_preserves_untouched_fields(service, base_file):
    dto = service.read()
    dto.prenom = "Cami"  # name recomposé "Cami Martin"
    dto.hard = dto.hard + ["Rust"]
    dto.experiences[0].titre = "Lead Developer"
    out = service.write(dto)
    assert out.prenom == "Cami" and out.experiences[0].titre == "Lead Developer"

    # Relecture par le VRAI loader (celui de la génération) -> base toujours valide.
    base = load_base(base_file)
    assert base.profile.name == "Cami Martin"
    assert "Rust" in base.hard_skills
    assert base.experiences[0].title == "Lead Developer"
    # Champs NON édités par le front : PRÉSERVÉS.
    assert [e.degree for e in base.education] == ["Master Informatique"], (
        "education préservée"
    )
    assert base.interests == ["Échecs", "Course à pied"], "interests préservés"
    assert base.profile.location == "Paris, France", "location préservée"
    assert base.profile.accroche_template == "Étudiant spécialisé en {focus}."
    assert base.experiences[0].pinned is True, "pinned préservé (non exposé au profil)"


def test_write_roundtrips_languages(service, base_file):
    out = service.write(service.read())
    base = load_base(base_file)
    assert [(lang.name, lang.level) for lang in base.languages] == [
        ("Français", "langue maternelle"),
        ("Anglais", "B2"),
    ], "langues round-trip via « Nom — Niveau »"
    assert out.langues == ["Français — langue maternelle", "Anglais — B2"]


def test_write_invalid_date_raises_profile_error(service):
    dto = service.read()
    dto.experiences[0].debut = "2022"  # format invalide (attendu YYYY-MM)
    with pytest.raises(ProfileError) as exc:
        service.write(dto)
    assert "date" in str(exc.value).lower()


def test_read_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ProfileService(YamlExperienceBase(tmp_path / "absent.yaml")).read()


# --- Routes API (GET / PUT) -------------------------------------------------


@pytest.fixture
def client(base_file):
    app.dependency_overrides[get_profile_service] = lambda: ProfileService(
        YamlExperienceBase(base_file)
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_get_profile(client):
    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["prenom"] == "Camille" and body["email"] == "camille@example.com"
    assert {
        "nom",
        "prenom",
        "telephone",
        "email",
        "soft",
        "hard",
        "langues",
        "experiences",
    } <= set(body)


def test_api_put_profile_persists(client, base_file):
    body = client.get("/profile").json()
    body["prenom"] = "Cami"
    body["soft"].append("Curiosité")
    r = client.put("/profile", json=body)
    assert r.status_code == 200 and r.json()["prenom"] == "Cami"
    base = load_base(base_file)
    assert base.profile.name == "Cami Martin" and "Curiosité" in base.soft_skills


def test_api_put_invalid_is_400(client):
    body = client.get("/profile").json()
    body["experiences"][0]["debut"] = "pas-une-date"
    r = client.put("/profile", json=body)
    assert r.status_code == 400 and "invalide" in r.json()["detail"]


def test_document_defaults_roundtrip(service, base_file):
    """Templates par défaut et lettre type : rangés dans la base, relus à l'identique."""
    dto = service.read()
    assert (dto.template_cv, dto.options_cv, dto.template_lettre) == ("", {}, "")
    dto.template_cv = "basic"
    dto.options_cv = {"couleur": "bleu"}
    dto.template_lettre = "moderne"
    dto.lettre_exemple = "  Madame, Monsieur,\n\nJe candidate…  "
    back = service.write(dto)
    assert back.template_cv == "basic" and back.options_cv["couleur"] == "bleu"
    assert back.template_lettre == "moderne"
    assert back.lettre_exemple == "Madame, Monsieur,\n\nJe candidate…"
    base = YamlExperienceBase(base_file).load()
    assert base.documents.cv_template is not None
    assert base.documents.cv_template.startswith("basic")


def test_unknown_default_template_is_refused(service):
    dto = service.read()
    dto.template_cv = "inexistant"
    with pytest.raises(ProfileError, match="template par défaut invalide"):
        service.write(dto)


def test_legacy_accroche_key_is_still_read():
    """Une base écrite avant le renommage (`accroche_gabarit`) garde son accroche."""
    from jobtool.core.experience_base.models import Profile

    legacy = Profile.model_validate(
        {"name": "A B", "email": "a@b.c", "accroche_gabarit": "Étudiant en {focus}."}
    )
    assert legacy.accroche_template == "Étudiant en {focus}."
    assert "accroche_template" in legacy.model_dump(exclude_none=True)
