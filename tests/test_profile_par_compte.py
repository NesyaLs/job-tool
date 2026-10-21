"""La base d'expériences est CLOISONNÉE par compte — la preuve, pas le raisonnement.

Pourquoi ce fichier existe. `ProfileService`, le générateur CV/lettre et l'édition de
documents lisaient tous les trois `settings.base_path`, c'est-à-dire UN fichier du serveur.
Les deux modes (fichier local, base partagée) fonctionnent isolément : rien, dans une suite
qui ne fait tourner qu'un seul compte, ne distingue « chacun son profil » de « tout le monde
le même ». Le défaut était donc invisible aux tests verts, et visible en production
uniquement — sous la forme du profil d'un client écrasé par celui d'un autre.

D'où deux comptes RÉELS sur un même moteur, et des écritures croisées.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from jobtool.api.deps import get_experience_base_repo, get_profile_service
from jobtool.api.main import app
from jobtool.core.db import connect_sqlite, ensure_user, make_engine
from jobtool.core.experience_base.store import (
    DbExperienceBase,
    ExperienceBaseMissing,
    YamlExperienceBase,
)
from jobtool.services.profile_service import ProfileDTO, ProfileService

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _dto(prenom: str, nom: str, hard: list[str]) -> ProfileDTO:
    """Un profil minimal mais VALIDE (identité + une expérience tracée)."""
    return ProfileDTO(
        nom=nom,
        prenom=prenom,
        email=f"{prenom.lower()}@exemple.fr",
        telephone="0600000000",
        hard=hard,
        experiences=[
            {
                "id": f"exp-{prenom.lower()}",
                "titre": "Développeur",
                "org": "Acme",
                "type": "job",
                "debut": "2022-01",
                "fin": "present",
                "resume": "",
                "realisations": [
                    {
                        "id": f"exp-{prenom.lower()}.1",
                        "text": "Une réalisation.",
                        "skills": [],
                    }
                ],
            }
        ],
    )


@pytest.fixture
def engine(tmp_path):
    """Un moteur partagé par les deux comptes, avec leurs deux lignes `users`."""
    db = tmp_path / "partagee.db"
    conn = connect_sqlite(db)
    ensure_user(conn, ALICE, email="alice@exemple.fr", name="Alice")
    ensure_user(conn, BOB, email="bob@exemple.fr", name="Bob")
    conn.commit()
    conn.close()
    return make_engine(f"sqlite:///{db}")


# --- Le dépôt lui-même -------------------------------------------------------


def test_deux_comptes_ne_partagent_pas_leur_base(engine):
    """Chacun écrit, chacun relit LE SIEN. C'est le test qui manquait."""
    alice = ProfileService(DbExperienceBase(engine, ALICE))
    bob = ProfileService(DbExperienceBase(engine, BOB))

    alice.write(_dto("Alice", "Martin", ["Python"]))
    bob.write(_dto("Bob", "Durand", ["Rust"]))

    assert alice.read().prenom == "Alice", "Alice relit son propre profil"
    assert bob.read().prenom == "Bob", "l'écriture de Bob n'a pas écrasé celle d'Alice"
    assert alice.read().hard == ["Python"]
    assert bob.read().hard == ["Rust"]


def test_ecrire_deux_fois_remplace_sans_dupliquer(engine):
    """La deuxième écriture d'un compte remplace la première (une ligne par compte)."""
    alice = ProfileService(DbExperienceBase(engine, ALICE))
    alice.write(_dto("Alice", "Martin", ["Python"]))
    alice.write(_dto("Alice", "Martin", ["Python", "SQL"]))
    assert alice.read().hard == ["Python", "SQL"]


def test_compte_sans_base_leve_une_absence(engine):
    """Un compte fraîchement inscrit n'a pas encore de base : absence, pas erreur obscure.

    `ExperienceBaseMissing` hérite de `FileNotFoundError`, ce qui fait emprunter à ce cas le
    chemin déjà éprouvé du fichier absent — jusqu'au 404 du routeur.
    """
    with pytest.raises(ExperienceBaseMissing):
        ProfileService(DbExperienceBase(engine, BOB)).read()
    with pytest.raises(FileNotFoundError):
        ProfileService(DbExperienceBase(engine, BOB)).read()


def test_le_yaml_reste_le_mode_mono_poste(tmp_path):
    """Sans moteur, la base vit dans un fichier — et l'écriture y est bien atomique."""
    chemin = tmp_path / "moi.yaml"
    service = ProfileService(YamlExperienceBase(chemin))
    service.write(_dto("Camille", "Martin", ["Python"]))
    assert chemin.exists(), "le fichier est créé"
    assert not chemin.with_name(chemin.name + ".tmp").exists(), (
        "pas de temporaire résiduel"
    )
    assert service.read().prenom == "Camille"


# --- Le câblage HTTP ---------------------------------------------------------


def test_les_routes_servent_le_profil_du_compte_appelant(engine):
    """Bout en bout : deux clients, deux identités, deux profils.

    On surcharge la dépendance de DÉPÔT (et non le service), c'est-à-dire exactement le
    point où l'identité entre dans le graphe. Un appel direct à la dépendance sortirait de
    ce graphe et ne serait plus surchargeable — c'est ce défaut-là qu'on verrouille ici.
    """

    def client_pour(user_id):
        app.dependency_overrides[get_experience_base_repo] = lambda: DbExperienceBase(
            engine, user_id
        )
        return TestClient(app)

    try:
        client_pour(ALICE).put(
            "/profile", json=_dto("Alice", "Martin", ["Python"]).model_dump()
        )
        client_pour(BOB).put(
            "/profile", json=_dto("Bob", "Durand", ["Rust"]).model_dump()
        )

        chez_alice = client_pour(ALICE).get("/profile")
        chez_bob = client_pour(BOB).get("/profile")
        assert chez_alice.status_code == 200 and chez_bob.status_code == 200
        assert chez_alice.json()["prenom"] == "Alice"
        assert chez_bob.json()["prenom"] == "Bob"
        assert chez_alice.json()["hard"] == ["Python"]
    finally:
        app.dependency_overrides.clear()


def test_profil_absent_donne_404(engine):
    """Compte connu, base pas encore renseignée -> 404, pas 500."""
    app.dependency_overrides[get_experience_base_repo] = lambda: DbExperienceBase(
        engine, BOB
    )
    try:
        assert TestClient(app).get("/profile").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_la_fabrique_du_service_recoit_bien_le_depot(engine):
    """`get_profile_service` prend le dépôt en dépendance, il ne le construit pas lui-même.

    Sans cela, la surcharge ci-dessus n'aurait aucun effet et le service retomberait sur le
    fichier du serveur — c'est-à-dire sur le défaut d'origine, silencieusement.
    """
    repo = DbExperienceBase(engine, ALICE)
    service = get_profile_service(base_repo=repo)
    assert isinstance(service, ProfileService)
    assert service._repo is repo
