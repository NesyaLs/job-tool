"""Profils multiples (administrateurs) : chaque profil a sa base, sa photo, son entretien,
ses documents — et rien ne passe d'un profil à l'autre, ni d'un compte à l'autre.

Les tests passent par les VRAIES routes et les vrais dépôts, sur une base SQLite partagée
par deux comptes : seules l'identité et la base sont surchargées (c'est-à-dire exactement les
points où elles entrent dans le graphe FastAPI).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from jobtool.api.deps import get_current_user_id, get_drafts_db, get_offers_db
from jobtool.api.main import app
from jobtool.core.db import connect_sqlite, ensure_user, make_engine, users
from jobtool.core.experience_base.models import ExperienceBase, Profile
from jobtool.core.experience_base.photo_store import PhotoStore, StoredPhoto
from jobtool.core.experience_base.profiles import (
    MAX_EXTRA_PROFILES,
    ExtraProfileBase,
    ProfileRegistry,
    resolve_active_profile,
)
from jobtool.core.experience_base.store import DbExperienceBase
from jobtool.documents.drafts import store as drafts
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.interview.models import InterviewSession
from jobtool.interview.store import InterviewSessionStore

ADMIN = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _base(name: str, skill: str) -> ExperienceBase:
    return ExperienceBase(
        profile=Profile(name=name, email=f"{skill.lower()}@exemple.fr"),
        hard_skills=[skill],
        experiences=[],
    )


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "partagee.db"
    conn = connect_sqlite(db)
    ensure_user(conn, ADMIN, email="admin@exemple.fr", name="Admin")
    ensure_user(conn, USER, email="user@exemple.fr", name="User")
    conn.execute(update(users).where(users.c.id == ADMIN).values(is_admin=True))
    conn.commit()
    conn.close()
    eng = make_engine(f"sqlite:///{db}")
    DbExperienceBase(eng, ADMIN).save(_base("Admin Principal", "Python"))
    DbExperienceBase(eng, USER).save(_base("User Principal", "Rust"))
    return eng


@pytest.fixture
def http(engine):
    def client_for(user_id: uuid.UUID) -> TestClient:
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        app.dependency_overrides[get_offers_db] = lambda: engine
        app.dependency_overrides[get_drafts_db] = lambda: engine
        return TestClient(app)

    yield client_for
    app.dependency_overrides.clear()


def _key_of(body: dict, name: str) -> str:
    return next(p["key"] for p in body["profiles"] if p["name"] == name)


# --- Droits -----------------------------------------------------------------------


def test_un_compte_non_admin_ne_voit_que_son_profil_principal(http):
    c = http(USER)
    body = c.get("/profiles").json()
    assert body["allowed"] is False and body["active"] == ""
    assert [p["name"] for p in body["profiles"]] == ["Profil principal"]
    assert c.post("/profiles", json={"name": "Autre"}).status_code == 403
    assert c.put("/profiles/active", json={"key": ""}).status_code == 403


def test_le_poste_local_a_droit_aux_profils(tmp_path):
    """Mono-poste : son seul utilisateur est son propriétaire."""
    from jobtool.core.db import LOCAL_USER_ID

    assert resolve_active_profile(str(tmp_path / "jobs.db"), LOCAL_USER_ID).allowed


def test_un_admin_qui_perd_ses_droits_retombe_sur_son_principal(http, engine):
    c = http(ADMIN)
    key = _key_of(c.post("/profiles", json={"name": "Data"}).json(), "Data")
    c.put("/profiles/active", json={"key": key})
    assert c.get("/profile").json()["prenom"] == ""  # profil « Data », vide

    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == ADMIN).values(is_admin=False))
    assert c.get("/profile").json()["prenom"] == "Admin", "retour au principal"
    assert ProfileRegistry(engine, ADMIN).list()[1].key == key, "profil conservé"


# --- Cloisonnement entre profils ------------------------------------------------------


def test_le_profil_actif_decide_de_la_base_lue_et_ecrite(http, engine):
    c = http(ADMIN)
    key = _key_of(c.post("/profiles", json={"name": "Data"}).json(), "Data")
    assert c.put("/profiles/active", json={"key": key}).json()["active"] == key

    dto = c.get("/profile").json()
    dto.update(prenom="Alex", nom="Data", hard=["SQL"])
    assert c.put("/profile", json=dto).status_code == 200

    assert c.get("/profile").json()["hard"] == ["SQL"]
    principal = DbExperienceBase(engine, ADMIN).load()
    assert principal.hard_skills == ["Python"], "le principal n'a pas bougé"

    c.put("/profiles/active", json={"key": ""})
    assert c.get("/profile").json()["hard"] == ["Python"]


def test_copier_un_profil_copie_sa_base_et_sa_photo(http, engine):
    PhotoStore(engine, ADMIN, profile_key="").save(StoredPhoto("abc", b"jpeg"))
    base = DbExperienceBase(engine, ADMIN).load()
    DbExperienceBase(engine, ADMIN).save(
        base.model_copy(
            update={"profile": base.profile.model_copy(update={"photo": "abc"})}
        )
    )
    c = http(ADMIN)
    key = _key_of(
        c.post("/profiles", json={"name": "Copie", "copy_from": ""}).json(), "Copie"
    )
    copied = ExtraProfileBase(engine, ADMIN, uuid.UUID(key))
    assert copied.load().hard_skills == ["Python"]
    assert copied.load_photo() == ("abc", b"jpeg")
    # La photo du profil copié est indépendante : la supprimer ne touche pas l'original.
    PhotoStore(engine, ADMIN, profile_key=key).delete()
    assert PhotoStore(engine, ADMIN, profile_key="").load() is not None


def test_chaque_profil_a_son_entretien(engine):
    key = ProfileRegistry(engine, ADMIN).create("Data", _base("A", "SQL"))
    InterviewSessionStore(engine, ADMIN, profile_key="").save(
        InterviewSession(context_notes=["principal"])
    )
    assert InterviewSessionStore(engine, ADMIN, profile_key=key).load() is None
    InterviewSessionStore(engine, ADMIN, profile_key=key).save(
        InterviewSession(context_notes=["data"])
    )
    loaded = InterviewSessionStore(engine, ADMIN, profile_key="").load()
    assert loaded is not None and loaded.context_notes == ["principal"]


def _draft(doc_id: str) -> DocumentDraft:
    now = drafts.now_iso()
    return DocumentDraft(
        doc_id=doc_id,
        offer_id="offre-1",
        kind="cv",
        template="classic",
        render_input={},
        created_at=now,
        updated_at=now,
    )


def test_un_document_n_est_retrouve_que_sous_son_profil(engine):
    """Rouvrir le CV d'une offre sous un autre profil ne doit PAS reprendre celui-ci : il
    serait contrôlé contre la mauvaise base d'expériences."""
    key = ProfileRegistry(engine, ADMIN).create("Data", _base("A", "SQL"))
    with engine.connect() as conn:
        drafts.save_draft(conn, ADMIN, _draft("doc-data"), profile_key=key)
        assert (
            drafts.latest_for_offer(conn, ADMIN, "offre-1", "cv", profile_key="")
            is None
        )
        assert drafts.get_draft(conn, ADMIN, "doc-data", profile_key="") is None
        found = drafts.latest_for_offer(conn, ADMIN, "offre-1", "cv", profile_key=key)
        assert found is not None and found.doc_id == "doc-data"


# --- Suppression --------------------------------------------------------------------


def test_supprimer_un_profil_emporte_son_entretien_et_ses_documents(
    http, engine, tmp_path
):
    c = http(ADMIN)
    key = _key_of(c.post("/profiles", json={"name": "Data"}).json(), "Data")
    c.put("/profiles/active", json={"key": key})
    InterviewSessionStore(engine, ADMIN, profile_key=key).save(InterviewSession())
    with engine.connect() as conn:
        drafts.save_draft(conn, ADMIN, _draft("doc-data"), profile_key=key)
        drafts.save_draft(conn, ADMIN, _draft("doc-principal"), profile_key="")

    body = c.delete(f"/profiles/{key}").json()
    assert body["active"] == "" and len(body["profiles"]) == 1
    assert InterviewSessionStore(engine, ADMIN, profile_key=key).load() is None
    with engine.connect() as conn:
        assert drafts.get_draft(conn, ADMIN, "doc-data", profile_key=key) is None
        assert (
            drafts.get_draft(conn, ADMIN, "doc-principal", profile_key="") is not None
        )
    assert resolve_active_profile(engine, ADMIN).key == ""


def test_le_profil_principal_ne_se_supprime_ni_ne_se_renomme(http):
    c = http(ADMIN)
    assert c.delete("/profiles/").status_code in (404, 405)
    assert c.patch("/profiles/pas-une-cle", json={"name": "x"}).status_code == 404


# --- Cloisonnement entre comptes ------------------------------------------------------


def test_un_compte_ne_peut_ni_lire_ni_activer_le_profil_d_un_autre(http, engine):
    key = ProfileRegistry(engine, ADMIN).create("Secret", _base("A", "SQL"))
    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == USER).values(is_admin=True))
    other = http(USER)
    assert other.put("/profiles/active", json={"key": key}).status_code == 404
    assert other.delete(f"/profiles/{key}").status_code == 404
    from jobtool.core.experience_base.store import ExperienceBaseMissing

    with pytest.raises(ExperienceBaseMissing):
        ExtraProfileBase(engine, USER, uuid.UUID(key)).load()
    assert [p.name for p in ProfileRegistry(engine, ADMIN).list()] == [
        "Profil principal",
        "Secret",
    ]


# --- Bornes -----------------------------------------------------------------------------


def test_nombre_et_nom_de_profils_bornes(http):
    c = http(ADMIN)
    assert c.post("/profiles", json={"name": "   "}).status_code == 400
    for i in range(MAX_EXTRA_PROFILES):
        assert c.post("/profiles", json={"name": f"P{i}"}).status_code == 200
    assert c.post("/profiles", json={"name": "de trop"}).status_code == 409


def test_l_entretien_suit_le_profil_actif(http, engine):
    """Câblage : la route /interview reçoit la clé du profil actif par `Depends`."""
    c = http(ADMIN)
    principal = c.get("/interview").json()
    assert "J'ai parcouru ton profil" in principal["messages"][0]["text"]

    key = _key_of(c.post("/profiles", json={"name": "Vierge"}).json(), "Vierge")
    c.put("/profiles/active", json={"key": key})
    fresh = c.get("/interview").json()
    assert "parle-moi de toi" in fresh["messages"][0]["text"].lower()
    assert InterviewSessionStore(engine, ADMIN, profile_key=key).load() is not None
    assert InterviewSessionStore(engine, ADMIN, profile_key="").load() is not None
