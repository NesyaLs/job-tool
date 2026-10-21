"""Propositions de puces pour une expérience : un appel, le code vérifie, rien n'est écrit.

Le client du modèle est simulé (aucun réseau). On vérifie ce qui part au modèle (rôle,
profil, consigne), ce que le code écarte (chiffres inventés, doublons, longueur), et que la
route ne touche pas au profil.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_current_user_id, get_llm_client_factory, get_offers_db
from jobtool.api.main import app
from jobtool.core.db import connect_sqlite, ensure_user, make_engine
from jobtool.core.experience_base.models import (
    Achievement,
    Experience,
    ExperienceBase,
    ExperienceType,
    Profile,
)
from jobtool.core.experience_base.store import DbExperienceBase
from jobtool.interview.bullets import (
    BulletSuggestions,
    TargetExperience,
    build_user_prompt,
    suggest_bullets,
)

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _Parsed:
    def __init__(self, parsed):
        self.choices = [
            type("C", (), {"message": type("M", (), {"parsed": parsed})()})()
        ]


class _FakeChat:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.seen: list[list[dict]] = []

    def parse(self, model, messages, response_format, temperature):  # noqa: ARG002
        self.seen.append([dict(m) for m in messages])
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Parsed(out)


class FakeClient:
    def __init__(self, *outputs):
        self.chat = _FakeChat(outputs)


def _base() -> ExperienceBase:
    return ExperienceBase(
        profile=Profile(name="Alice Martin", email="alice@exemple.fr"),
        hard_skills=["Python", "SQL"],
        experiences=[
            Experience(
                id="stage",
                type=ExperienceType.internship,
                title="Stage data",
                organization="Acme",
                start="2025-02",
                end="2025-08",
                achievements=[
                    Achievement(
                        id="stage.1", text="Automatisé 12 rapports hebdomadaires"
                    )
                ],
            ),
            Experience(
                id="projet",
                type=ExperienceType.school_project,
                title="Projet de fin d'études",
                organization="École",
                start="2024-09",
                end="2025-01",
            ),
        ],
    )


TARGET = TargetExperience(
    id="projet",
    title="Projet de fin d'études",
    organization="École",
    type="school_project",
    bullets=["Conçu un tableau de bord de suivi des ventes"],
)

GOOD = [
    "Construit un pipeline de nettoyage des données de ventes en Python",
    "Présenté les résultats du projet devant un jury d'enseignants",
    "Rédigé la documentation technique de l'application livrée",
]


def test_le_prompt_porte_le_role_le_profil_et_la_consigne():
    prompt = build_user_prompt(_base(), TARGET, "insiste sur le travail en équipe")
    assert "Stage data" in prompt and "Automatisé 12 rapports" in prompt
    assert "Python, SQL" in prompt
    assert "EXPÉRIENCE CIBLE" in prompt and "Projet de fin d'études" in prompt
    assert "CONSIGNE DE LA PERSONNE : insiste sur le travail en équipe" in prompt
    # Pas de coordonnées : elles n'aident pas à proposer.
    assert "alice@exemple.fr" not in prompt


def test_un_chiffre_invente_est_redemande_puis_ecarte():
    client = FakeClient(
        BulletSuggestions(bullets=[*GOOD, "Réduit de 40 % le temps de traitement"]),
        BulletSuggestions(bullets=[*GOOD, "Réduit de 40 % le temps de traitement"]),
    )
    kept = suggest_bullets(client, _base(), TARGET)
    assert kept == GOOD
    correction = client.chat.seen[1][-1]["content"]
    assert "chiffre absent" in correction and "40" in correction


def test_un_chiffre_donne_dans_la_consigne_est_permis():
    bullet = "Réduit de 40 % le temps de traitement des données de ventes"
    client = FakeClient(BulletSuggestions(bullets=[*GOOD, bullet]))
    kept = suggest_bullets(client, _base(), TARGET, "le temps a baissé de 40 %")
    assert bullet in kept
    assert len(client.chat.seen) == 1, "rien à corriger : un seul appel"


def test_une_puce_deja_ecrite_n_est_pas_reproposee():
    client = FakeClient(
        BulletSuggestions(bullets=[*GOOD, TARGET.bullets[0]]),
        BulletSuggestions(bullets=[*GOOD, TARGET.bullets[0]]),
    )
    assert suggest_bullets(client, _base(), TARGET) == GOOD


# --- Route ----------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "partagee.db"
    conn = connect_sqlite(db)
    ensure_user(conn, ALICE, email="alice@exemple.fr", name="Alice")
    conn.commit()
    conn.close()
    engine = make_engine(f"sqlite:///{db}")
    DbExperienceBase(engine, ALICE).save(_base())

    def client_for(*outputs) -> tuple[TestClient, FakeClient]:
        fake = FakeClient(*outputs)
        app.dependency_overrides[get_current_user_id] = lambda: ALICE
        app.dependency_overrides[get_offers_db] = lambda: engine
        app.dependency_overrides[get_llm_client_factory] = lambda: lambda: fake
        return TestClient(app), fake

    yield client_for, engine
    app.dependency_overrides.clear()


def _body(instruction: str = "") -> dict:
    return {
        "experience": {
            "id": "nouvelle",
            "titre": "Bénévolat",
            "org": "Association",
            "type": "job",
            "realisations": [],
        },
        "instruction": instruction,
    }


def test_route_renvoie_les_puces_sans_toucher_au_profil(api):
    client_for, engine = api
    http, fake = client_for(BulletSuggestions(bullets=GOOD))
    before = DbExperienceBase(engine, ALICE).load()

    response = http.post("/profile/suggestions", json=_body("reste sobre"))

    assert response.status_code == 200, response.text
    assert response.json() == {"bullets": GOOD}
    # L'expérience non enregistrée est bien celle envoyée ; le reste du profil sert de contexte.
    sent = fake.chat.seen[0][1]["content"]
    assert "Bénévolat" in sent and "Stage data" in sent and "reste sobre" in sent
    assert DbExperienceBase(engine, ALICE).load() == before


def test_route_502_si_le_modele_echoue(api):
    client_for, _ = api
    http, _ = client_for(RuntimeError("réseau coupé"))
    response = http.post("/profile/suggestions", json=_body())
    assert response.status_code == 502
    assert "réseau coupé" in response.json()["detail"]
