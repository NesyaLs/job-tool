"""Entretien guidé par LLM : le LLM propose, le code vérifie, l'utilisateur décide.

Aucun appel réseau : le client Mistral est simulé (`FakeClient`), et rend des réponses
préparées à l'avance. Ce qui est vérifié est ce qui ARRIVE dans la base d'expériences et dans
la session — jamais une couche d'écriture simulée.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtool.api.deps import (
    get_current_user_id,
    get_llm_client_factory,
    get_offers_db,
)
from jobtool.api.main import app
from jobtool.core.db import connect_sqlite, ensure_user, llm_usage, make_engine
from jobtool.core.experience_base.models import (
    Achievement,
    Experience,
    ExperienceBase,
    ExperienceType,
    Profile,
)
from jobtool.core.experience_base.store import DbExperienceBase
from jobtool.interview import engine as eng
from jobtool.interview.checks import check_turn, invented_numbers, numbers_in
from jobtool.interview.gaps import find_gaps
from jobtool.interview.models import InterviewSession, Message, ProposalKind, Section
from jobtool.interview.prompt import HISTORY_WINDOW, SUMMARY_EVERY, system_prompt
from jobtool.interview.schema import ContextSummary, InterviewTurn, ProposalDraft
from jobtool.interview.store import InterviewSessionStore
from jobtool.services.interview_service import InterviewService

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")


# --- Client simulé -------------------------------------------------------------


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
        if not self._outputs:
            raise AssertionError("appel au modèle non prévu par le test")
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, response_format), (out, response_format)
        return _Parsed(out)


class FakeClient:
    """`client.chat.parse` rend les sorties préparées, une par appel."""

    def __init__(self, *outputs):
        self.chat = _FakeChat(outputs)

    @property
    def calls(self) -> int:
        return len(self.chat.seen)


def turn(message="Et ensuite ?", *proposals, complete=False, notes=()):
    return InterviewTurn(
        message=message,
        proposals=list(proposals),
        section_complete=complete,
        context_notes=list(notes),
    )


def exp_draft(**kw):
    fields = dict(
        kind=ProposalKind.experience,
        experience_type=ExperienceType.school_project,
        title="Application de gestion de bibliothèque",
        organization="INSA Lyon",
        start="2024-09",
        end="2025-01",
        sources=["m2"],
    )
    fields.update(kw)
    return ProposalDraft(**fields)


def rea_draft(ref, text, **kw):
    return ProposalDraft(
        kind=ProposalKind.realisation, experience_ref=ref, text=text, **kw
    )


def session_with(*user_texts: str) -> InterviewSession:
    """Session démarrée sur profil vide, avec des messages utilisateur déjà échangés."""
    s = eng.start_session(None)
    for text in user_texts:
        s.messages.append(
            Message(id=s.next_message_id(), role="user", text=text, section=s.current)
        )
        s.messages.append(
            Message(
                id=s.next_message_id(), role="assistant", text="ok", section=s.current
            )
        )
    return s


def a_base() -> ExperienceBase:
    return ExperienceBase(
        profile=Profile(name="Camille Martin", email="c@exemple.fr"),
        hard_skills=["PostgreSQL", "Python"],
        experiences=[
            Experience(
                id="stage-acme-2024",
                type=ExperienceType.internship,
                title="Stage développeur",
                organization="Acme",
                start="2024-02",
                end="2024-07",
                summary="Stage backend.",
                achievements=[
                    Achievement(id="stage-acme-2024.1", text="Écriture d'une API REST")
                ],
            ),
            Experience(
                id="projet-robot-2023",
                type=ExperienceType.personal_project,
                title="Robot suiveur de ligne",
                organization="Projet personnel",
                start="2023-03",
                end="2023-06",
            ),
        ],
    )


# --- Prompt -------------------------------------------------------------------


def test_le_prompt_systeme_est_un_fichier_du_paquet():
    text = system_prompt()
    assert "N'invente rien" in text and "une seule question" in text.lower()


# --- Validation des propositions ----------------------------------------------


def test_une_proposition_valide_est_retenue_avec_sa_source():
    s = session_with(
        "J'ai fait un projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025"
    )
    report = check_turn(turn("Top !", exp_draft()), s, None, next_index=1)
    assert report.issues == []
    [p] = report.proposals
    assert p.id == "p1" and p.kind is ProposalKind.experience
    assert p.section is Section.projets, "un projet d'école est rangé dans « projets »"
    assert p.sources == ["m2"]
    assert p.payload["start"] == "2024-09" and not p.blocking


def test_une_proposition_invalide_est_ecartee_avec_un_constat_lisible():
    s = session_with("Un projet en 2024")
    bad = ProposalDraft(kind=ProposalKind.experience, title="Projet", start="2024")
    report = check_turn(turn("ok", bad), s, None, next_index=1)
    assert report.proposals == []
    text = " ".join(report.issues)
    assert "organization" in text and "YYYY-MM" in text


def test_une_realisation_doit_citer_une_experience_connue():
    s = session_with("J'ai codé une API")
    report = check_turn(
        turn("ok", rea_draft("inconnue", "Écriture d'une API")), s, None, next_index=1
    )
    assert report.proposals == [] and "experience_ref" in report.issues[0]


def test_une_realisation_peut_citer_l_experience_proposee_dans_la_meme_reponse():
    """Les ids sont attribués par POSITION : le LLM peut prévoir « p1 »."""
    s = session_with(
        "Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025, j'ai fait la "
        "base de données"
    )
    report = check_turn(
        turn("ok", exp_draft(), rea_draft("p1", "Conception de la base de données")),
        s,
        None,
        next_index=1,
    )
    assert report.issues == []
    assert [p.id for p in report.proposals] == ["p1", "p2"]


def test_une_source_inexistante_est_remplacee_par_le_dernier_message_utilisateur():
    s = session_with("Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025")
    report = check_turn(
        turn("ok", exp_draft(sources=["m99", "m1"])), s, None, next_index=1
    )
    assert report.proposals[0].sources == ["m2"], "m1 est un message de l'assistant"


def test_un_message_a_plusieurs_questions_est_signale():
    s = session_with("bonjour")
    report = check_turn(turn("Tu as fait quoi ? Et où ?"), s, None, next_index=1)
    assert any("UNE seule question" in i for i in report.issues)


# --- Garde-fou anti-invention ---------------------------------------------------


def test_numbers_in_normalise_chiffres_et_nombres_en_lettres():
    assert numbers_in("une base de douze tables, 1 200 lignes, 3,50 €") >= {
        "12",
        "1200",
        "3.5",
        "1",
    }


def test_un_nombre_absent_des_messages_est_signale():
    corpus = "J'ai conçu la base de données du projet"
    issues = invented_numbers(
        ProposalKind.realisation,
        {"experience_ref": "p1", "text": "Conception d'une base SQL de 12 tables"},
        corpus,
    )
    assert issues and "12" in issues[0]


def test_un_nombre_donne_en_toutes_lettres_n_est_pas_signale():
    corpus = "J'ai conçu une base de douze tables"
    assert (
        invented_numbers(
            ProposalKind.realisation,
            {"experience_ref": "p1", "text": "Conception d'une base SQL de 12 tables"},
            corpus,
        )
        == []
    )


def test_une_annee_calculee_par_le_llm_est_signalee():
    """« l'an dernier » converti en 2025 : un calcul du LLM, interdit."""
    s = session_with("J'ai fait un projet de bibliothèque l'an dernier à l'INSA")
    report = check_turn(turn("ok", exp_draft()), s, None, next_index=1)
    [p] = report.proposals
    assert p.blocking and any("2024" in w for w in p.warnings)


def test_le_garde_fou_passe_par_une_correction_puis_bloque_si_elle_echoue():
    """Le LLM invente « 12 » deux fois : la proposition est gardée mais BLOQUÉE."""
    s = session_with("Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025")
    s.proposals.clear()
    invented = turn("Je note !", exp_draft(), rea_draft("p1", "Base SQL de 12 tables"))
    client = FakeClient(invented, invented)
    out = eng.run_turn(s, None, "J'ai fait toute la base de données", client)
    assert client.calls == 2, "une correction a été demandée"
    correction = client.chat.seen[1][-1]["content"]
    assert "12" in correction and "DEMANDE" in correction
    rea = out.proposals[-1]
    assert rea.kind is ProposalKind.realisation and rea.blocking


def test_la_correction_du_llm_debloque_la_proposition():
    s = session_with("Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025")
    invented = turn("ok", exp_draft(), rea_draft("p1", "Base SQL de 12 tables"))
    fixed = turn(
        "Combien de tables comptait la base ?",
        exp_draft(),
        rea_draft("p1", "Conception de la base de données SQL"),
    )
    out = eng.run_turn(
        s, None, "J'ai fait la base de données", FakeClient(invented, fixed)
    )
    assert not any(p.blocking for p in out.proposals)
    assert out.messages[-1].text == "Combien de tables comptait la base ?"


def test_une_soft_skill_sans_exemple_concret_est_refusee():
    s = session_with("J'ai animé les réunions de mon équipe")
    soft = ProposalDraft(
        kind=ProposalKind.competence, name="Leadership", category="soft"
    )
    report = check_turn(turn("ok", soft), s, None, next_index=1)
    assert report.proposals == [] and "evidence" in report.issues[0]
    soft.evidence = "a animé les réunions hebdomadaires de son équipe (m2)"
    assert check_turn(turn("ok", soft), s, None, next_index=1).proposals


def test_une_competence_deja_presente_est_dedoublonnee():
    s = session_with("J'ai utilisé postgresql")
    base = a_base()
    dup = ProposalDraft(
        kind=ProposalKind.competence, name="postgreSQL", category="hard"
    )
    report = check_turn(turn("ok", dup), s, base, next_index=1)
    assert report.proposals == [] and report.dropped
    # … sauf pour la LIER à une réalisation : on garde l'orthographe existante.
    dup.realisation_ref = "stage-acme-2024.1"
    [p] = check_turn(turn("ok", dup), s, base, next_index=1).proposals
    assert p.payload["name"] == "PostgreSQL"


# --- Acceptation / modification / refus ---------------------------------------


def _pending_session():
    s = session_with("Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025")
    client = FakeClient(
        turn(
            "Tu as utilisé quel langage ?",
            exp_draft(),
            rea_draft("p1", "Conception de la base de données", skills=["SQL"]),
            ProposalDraft(
                kind=ProposalKind.competence,
                name="SQL",
                category="hard",
                realisation_ref="p2",
            ),
        )
    )
    return eng.run_turn(s, None, "J'ai conçu la base de données en SQL", client)


def test_accepter_en_lot_ecrit_experience_realisation_et_competence_liees():
    s = _pending_session()
    s2, base = eng.accept(s, None, [("p3", None), ("p2", None), ("p1", None)])
    [exp] = base.experiences
    assert exp.id == "application-de-gestion-de-bibliotheque-2024"
    [ach] = exp.achievements
    assert ach.id == f"{exp.id}.1" and ach.text == "Conception de la base de données"
    assert ach.skills == ["SQL"]
    assert base.hard_skills == ["SQL"]
    assert all(p.status == "accepted" for p in s2.proposals)
    # Traçabilité : chaque élément créé renvoie au message utilisateur qui le fonde.
    assert s2.provenance[exp.id] == ["m2"]
    assert s2.provenance[ach.id] == [s2.proposal("p2").sources[0]]
    assert s2.proposal("p2").result_id == ach.id
    assert s.proposals[0].status == "pending", "la session d'origine n'est pas modifiée"


def test_accepter_une_realisation_avant_son_experience_est_refuse():
    s = _pending_session()
    with pytest.raises(eng.InterviewError, match="accepte d'abord l'expérience"):
        eng.accept(s, None, [("p2", None)])


def test_une_reference_a_une_proposition_deja_acceptee_est_resolue():
    s = _pending_session()
    s, base = eng.accept(s, None, [("p1", None)])
    s, base = eng.accept(s, base, [("p2", None)])
    assert base.experiences[0].achievements[0].id.endswith(".1")


def test_modifier_puis_accepter_ecrit_la_version_modifiee():
    s = _pending_session()
    s, base = eng.accept(
        s, None, [("p1", {**s.proposal("p1").payload, "title": "Appli bibliothèque"})]
    )
    assert base.experiences[0].title == "Appli bibliothèque"
    assert s.proposal("p1").edited


def test_une_modification_invalide_est_refusee_sans_rien_ecrire():
    s = _pending_session()
    with pytest.raises(eng.InterviewError, match="YYYY-MM"):
        eng.accept(s, None, [("p1", {**s.proposal("p1").payload, "start": "sept"})])


def test_une_proposition_bloquee_ne_s_accepte_qu_apres_modification():
    s = session_with("Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025")
    bad = turn("ok", exp_draft(), rea_draft("p1", "Base SQL de 12 tables"))
    s = eng.run_turn(s, None, "J'ai fait la base", FakeClient(bad, bad))
    s, base = eng.accept(s, None, [("p1", None)])
    with pytest.raises(eng.InterviewError, match="modifie-la"):
        eng.accept(s, base, [("p2", None)])
    edited = {**s.proposal("p2").payload, "text": "Base SQL de 8 tables"}
    s, base = eng.accept(s, base, [("p2", edited)])
    assert base.experiences[0].achievements[0].text == "Base SQL de 8 tables"


def test_refuser_garde_la_trace_et_n_ecrit_rien():
    s = _pending_session()
    s = eng.reject(s, ["p1"])
    assert s.proposal("p1").status == "rejected"
    from jobtool.interview.prompt import state_block

    assert "propositions_refusees" in state_block(s, None, [])
    with pytest.raises(eng.InterviewError, match="déjà traitée"):
        eng.accept(s, None, [("p1", None)])


def test_accepter_une_identite_cree_la_base_d_un_compte_neuf():
    s = session_with("Je m'appelle Camille Martin")
    ident = ProposalDraft(
        kind=ProposalKind.identite, field="name", value="Camille Martin"
    )
    s = eng.run_turn(s, None, "c'est ça", FakeClient(turn("Ton e-mail ?", ident)))
    s, base = eng.accept(s, None, [(s.proposals[0].id, None)])
    assert base.profile.name == "Camille Martin" and base.experiences == []


# --- Manques d'un profil existant -----------------------------------------------


def test_les_manques_d_un_profil_existant_sont_detectes():
    kinds = {(g.kind, g.target_id) for g in find_gaps(a_base())}
    assert ("no_achievement", "projet-robot-2023") in kinds
    assert ("achievement_without_skills", "stage-acme-2024") in kinds
    assert ("empty_section", None) in kinds  # aucune formation
    assert ("few_hard_skills", None) in kinds
    assert ("no_soft_skill", None) in kinds


def test_profil_vide_l_entretien_commence_par_parle_moi_de_toi():
    s = eng.start_session(None)
    assert s.current is Section.situation
    assert "parle-moi de toi" in s.messages[0].text.lower()
    assert all(v in ("todo", "current") for v in s.sections.values())


def test_profil_existant_l_entretien_commence_par_les_manques():
    base = a_base()
    base.languages = []
    base.profile.phone, base.profile.location = "0600000000", "Lyon"
    from jobtool.core.experience_base.models import Language

    base.languages = [Language(name="Anglais", level="B2")]
    s = eng.start_session(base)
    assert s.current is Section.formation, "situation complète : on va à la formation"
    assert s.sections[Section.situation] == "done"
    assert "aucune formation" in s.messages[0].text


# --- Plan ---------------------------------------------------------------------------


def test_sauter_revenir_et_progression():
    s = eng.start_session(None)
    eng.skip_section(s, Section.situation)
    assert s.current is Section.formation and eng.progress(s) == (1, 5)
    eng.goto_section(s, Section.situation)
    assert s.current is Section.situation and s.sections[Section.formation] == "todo"
    s2 = eng.run_turn(
        s, None, "Je suis en M2", FakeClient(turn("Et ta formation ?", complete=True))
    )
    assert s2.sections[Section.situation] == "done" and s2.current is Section.formation


def test_le_plafond_de_tours_est_respecte():
    s = eng.start_session(None)
    s.turns = eng.MAX_TURNS
    with pytest.raises(eng.TurnLimitReached):
        eng.run_turn(s, None, "encore", FakeClient())


# --- Coût : résumé de l'historique ---------------------------------------------------


def test_l_historique_long_est_resume_et_la_fenetre_bornee():
    s = session_with(
        *[f"réponse {i}" for i in range((HISTORY_WINDOW + SUMMARY_EVERY) // 2)]
    )
    client = FakeClient(ContextSummary(summary="Résumé des faits."), turn("Suite ?"))
    out = eng.run_turn(s, None, "encore une", client)
    assert out.summary == "Résumé des faits." and out.summary_upto > 0
    sent = client.chat.seen[1]
    assert len(sent) - 1 <= HISTORY_WINDOW + 1, "seule la fenêtre récente part au LLM"
    assert "Résumé des faits." in sent[0]["content"]
    assert sent[1]["role"] == "user", (
        "l'historique commence par une question de l'utilisateur"
    )


# --- Réponse invalide ---------------------------------------------------------------


def test_une_reponse_inexploitable_est_reessayee_puis_signalee_sans_casser_la_session():
    s = eng.start_session(None)
    client = FakeClient(RuntimeError("pas de structure"), RuntimeError("encore"))
    with pytest.raises(eng.InterviewLLMError, match="renvoie-le"):
        eng.run_turn(s, None, "Bonjour", client)
    assert len(s.messages) == 1, "la session n'a pas été modifiée"
    ok = eng.run_turn(
        s, None, "Bonjour", FakeClient(RuntimeError("x"), turn("Salut !"))
    )
    assert ok.messages[-1].text == "Salut !"


# --- Bout en bout : API, persistance, reprise, cloisonnement -------------------------


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "partagee.db"
    conn = connect_sqlite(db)
    ensure_user(conn, ALICE, email="alice@exemple.fr", name="Alice")
    ensure_user(conn, BOB, email="bob@exemple.fr", name="Bob")
    conn.commit()
    conn.close()
    return make_engine(f"sqlite:///{db}")


@pytest.fixture
def api(engine):
    """Client HTTP pour un compte donné. On surcharge l'IDENTITÉ, la BASE et le client du
    modèle — le reste du graphe (dépôts, service) est le vrai."""
    fakes: dict[uuid.UUID, FakeClient] = {}

    def client_for(user_id: uuid.UUID, *outputs) -> TestClient:
        fakes[user_id] = FakeClient(*outputs)
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        app.dependency_overrides[get_offers_db] = lambda: engine
        app.dependency_overrides[get_llm_client_factory] = lambda: (
            lambda: fakes[user_id]
        )
        return TestClient(app)

    yield client_for
    app.dependency_overrides.clear()


def test_la_session_est_sauvegardee_et_reprise(api, engine):
    http = api(
        ALICE,
        turn(
            "Super ! Tu as utilisé quel langage ?",
            exp_draft(sources=["m2"]),
        ),
    )
    first = http.get("/interview").json()
    assert "parle-moi de toi" in first["messages"][0]["text"].lower()
    assert first["profile"] is None

    r = http.post(
        "/interview/messages",
        json={
            "text": "Projet de bibliothèque à l'INSA de septembre 2024 à janvier 2025"
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["proposals"][0]["status"] == "pending"

    # « Quitter et revenir » : un nouveau client, aucune mémoire côté navigateur.
    again = api(ALICE).get("/interview").json()
    assert [m["id"] for m in again["messages"]] == ["m1", "m2", "m3"]
    assert again["proposals"][0]["id"] == "p1"

    accepted = api(ALICE).post(
        "/interview/proposals/accept", json={"decisions": [{"id": "p1"}]}
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["profile"]["experiences"][0]["title"].startswith("Application")
    # La base est réellement écrite, pour ce compte.
    stored = DbExperienceBase(engine, ALICE).load()
    assert stored.experiences[0].id in body["provenance"]
    # La consommation du tour est comptée.
    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(llm_usage)).scalar()
    assert n == 1


def test_deux_comptes_ont_chacun_leur_entretien(api, engine):
    api(ALICE, turn("Et ensuite ?")).post(
        "/interview/messages", json={"text": "Alice ici"}
    )
    bob = api(BOB).get("/interview").json()
    assert len(bob["messages"]) == 1, "Bob ne voit pas la conversation d'Alice"
    assert InterviewSessionStore(engine, BOB, profile_key="").load() is not None
    alice = InterviewSessionStore(engine, ALICE, profile_key="").load()
    assert alice is not None and alice.messages[1].text == "Alice ici"


def test_les_erreurs_ont_des_statuts_clairs(api):
    http = api(ALICE, RuntimeError("down"), RuntimeError("down"))
    r = http.post("/interview/messages", json={"text": "Bonjour"})
    assert r.status_code == 502 and "renvoie-le" in r.json()["detail"]
    assert len(http.get("/interview").json()["messages"]) == 1
    r = http.post("/interview/proposals/accept", json={"decisions": [{"id": "p9"}]})
    assert r.status_code == 400
    r = http.post("/interview/sections/competences/skip")
    assert r.json()["sections"][-1]["status"] == "skipped"


def test_recommencer_garde_le_profil(api, engine):
    DbExperienceBase(engine, ALICE).save(a_base())
    http = api(ALICE, turn("ok"))
    http.post("/interview/messages", json={"text": "salut"})
    r = http.post("/interview/restart").json()
    assert (
        len(r["messages"]) == 1 and r["profile"]["profile"]["name"] == "Camille Martin"
    )


def test_le_service_ne_cree_pas_de_client_pour_lire_ou_trancher(engine):
    def boom():
        raise AssertionError("aucun client ne doit être créé")

    service = InterviewService(
        DbExperienceBase(engine, ALICE),
        InterviewSessionStore(engine, ALICE, profile_key=""),
        boom,
    )
    view = service.view()
    assert view.progress_total == 5 and not view.finished


def test_le_domaine_entretien_ne_depend_que_de_core():
    """Sens des dépendances (project_ctx.md) : un domaine n'importe ni services/, ni une couche
    d'interface, ni un autre domaine."""
    import ast
    from pathlib import Path

    import jobtool.interview

    root = Path(jobtool.interview.__file__).parent
    forbidden = ("jobtool.services", "jobtool.api", "jobtool.cli", "jobtool.aggregator",
                 "jobtool.documents")  # fmt: skip
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            offenders += [f"{path.name}: {n}" for n in names if n.startswith(forbidden)]
    assert offenders == []
