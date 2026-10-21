"""Journal des échanges avec le modèle : capture, purge, et routes d'administration.

Aucun réseau : un faux client Mistral rend des sorties préparées, mais TOUT le reste est le
vrai chemin — `call_structured`, la boucle de correction, le point d'accroche
`core/llm/capture` et les écritures en base. C'est délibéré : un test qui mockerait la
couche d'écriture ne prouverait rien sur ce qui est écrit.

Ce qui est prouvé :
  1. une génération laisse une ligne et UN échange par appel, dans l'ordre, avec l'étape,
     la tentative, les messages envoyés et la réponse ;
  2. une génération qui échoue est notée en erreur, et l'exception remonte intacte ;
  3. hors d'une génération enregistrée (CLI, batch), rien n'est écrit ;
  4. un collecteur qui échoue ne casse pas la génération ;
  5. la purge garde 10 générations PAR TYPE, tous comptes confondus, sans échange orphelin ;
  6. les routes /admin/llm-runs sont réservées à l'administration, la liste ne porte aucun
     prompt et le détail les rend dans l'ordre.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select

from jobtool.api.deps import get_admin_service, get_current_user_id
from jobtool.api.main import app
from jobtool.core.db import (
    LOCAL_USER_ID,
    connect_sqlite,
    engine_for_sqlite_file,
    ensure_user,
    llm_exchanges,
    llm_runs,
    users,
)
from jobtool.core.llm.capture import ExchangeRecord, capture_exchanges, publish
from jobtool.core.llm.client import call_structured
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.services.admin_service import AdminService
from jobtool.services.llm_journal_service import (
    JOURNAL_KEEP_PER_KIND,
    DbGenerationJournal,
    noop_journal,
)

OTHER_USER = uuid.UUID("33333333-3333-3333-3333-333333333333")
SECRET = "Base d'expériences : stage en laboratoire."


class Answer(BaseModel):
    """Sortie structurée minimale, à la place d'un CV ou d'un routage."""

    texte: str


class _FakeChat:
    """`client.chat.parse` : rend les sorties préparées, une par appel."""

    def __init__(self, outputs: list[Answer]) -> None:
        self.outputs = list(outputs)
        self.seen: list[list[dict]] = []

    def parse(self, model, messages, response_format, temperature):  # noqa: ARG002
        self.seen.append([dict(m) for m in messages])
        parsed = self.outputs.pop(0)
        message = type("Message", (), {"parsed": parsed})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class FakeClient:
    def __init__(self, *outputs: str) -> None:
        self.chat = _FakeChat([Answer(texte=t) for t in outputs])


@pytest.fixture
def db(tmp_path):
    """Base SQLite jetable, schéma créé et compte local présent."""
    path = tmp_path / "journal.db"
    connect_sqlite(path).close()
    return engine_for_sqlite_file(path)


@pytest.fixture
def journal(db):
    return DbGenerationJournal(db=db, user_id=LOCAL_USER_ID)


def _rows(db, table, **where):
    stmt = select(table)
    for column, value in where.items():
        stmt = stmt.where(table.c[column] == value)
    with db.connect() as conn:
        return list(conn.execute(stmt.order_by(*table.primary_key.columns)))


def _count(db, table) -> int:
    with db.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)


def _generate(client, *, step: str, fails_once: bool) -> None:
    """Une « génération » : la vraie boucle de correction, avec un contrôle qui râle une fois."""
    seen: list[int] = []

    def check(_answer):
        seen.append(1)
        return ["trop long"] if fails_once and len(seen) == 1 else []

    run_correction_loop(
        client,
        [{"role": "system", "content": SECRET}, {"role": "user", "content": "Offre."}],
        Answer,
        check=check,
        build_correction=lambda issues: f"corrige : {issues}",
        step=step,
    )


# --- 1. une génération, ses appels ------------------------------------------------


def test_each_call_of_a_generation_is_recorded(db, journal):
    client = FakeClient("brouillon", "corrigé")

    with journal.record("cv", offer_id="h-a", template="compact"):
        _generate(client, step="cv_a", fails_once=True)

    (run,) = _rows(db, llm_runs)
    assert (run.kind, run.template, run.offer_id) == ("cv", "compact", "h-a")
    assert (run.status, run.calls, run.error) == ("ok", 2, None)
    assert run.model and run.ended_at

    first, second = _rows(db, llm_exchanges)
    assert [x.seq for x in (first, second)] == [1, 2]
    assert [x.step for x in (first, second)] == ["cv_a", "cv_a"]
    assert [x.attempt for x in (first, second)] == [1, 2]
    # Les messages enregistrés sont EXACTEMENT ceux passés au client…
    assert first.messages == client.chat.seen[0]
    # …et le second appel contient bien la correction demandée.
    assert second.messages[-1]["content"].startswith("corrige :")
    assert first.response == {"texte": "brouillon"}
    assert second.response == {"texte": "corrigé"}


def test_failed_generation_is_recorded_and_raises(db, journal):
    with pytest.raises(RuntimeError, match="modèle indisponible"):
        with journal.record("letter"):
            raise RuntimeError("modèle indisponible")

    (run,) = _rows(db, llm_runs)
    assert run.status == "error"
    assert "modèle indisponible" in run.error
    assert run.calls == 0


def test_partial_generation_keeps_the_calls_already_made(db, journal):
    client = FakeClient("brouillon")

    with pytest.raises(RuntimeError):
        with journal.record("cv"):
            _generate(client, step="cv_a", fails_once=False)
            raise RuntimeError("rendu impossible")

    (run,) = _rows(db, llm_runs)
    assert run.status == "error" and run.calls == 1
    assert len(_rows(db, llm_exchanges)) == 1


# --- 2. hors journal, et robustesse -----------------------------------------------


def test_nothing_is_written_without_a_journal(db):
    """CLI, batch, tests de rendu : aucun collecteur installé, aucune ligne écrite."""
    client = FakeClient("réponse")

    with noop_journal.record("cv"):
        _generate(client, step="cv_a", fails_once=False)

    assert _count(db, llm_runs) == 0 and _count(db, llm_exchanges) == 0


def test_a_failing_sink_never_breaks_the_generation():
    """Une base injoignable ne doit pas faire perdre une génération déjà payée."""

    def broken(_record):
        raise RuntimeError("base injoignable")

    with capture_exchanges(broken):
        publish(ExchangeRecord("m", "cv_a", 1, 0, [], None))  # ne lève pas

    client = FakeClient("réponse")
    with capture_exchanges(broken):
        answer = call_structured(client, [{"role": "user", "content": "x"}], Answer)
    assert answer.texte == "réponse"


def test_the_sink_does_not_leak_outside_its_block(db, journal):
    client = FakeClient("dedans", "dehors")

    with journal.record("cv"):
        _generate(client, step="cv_a", fails_once=False)
    call_structured(client, [{"role": "user", "content": "hors journal"}], Answer)

    assert _count(db, llm_exchanges) == 1


# --- 3. purge ---------------------------------------------------------------------


def test_purge_keeps_the_last_ten_of_each_kind(db, journal):
    """12 CV et 3 lettres -> 10 CV (les plus récents) et 3 lettres, sans échange orphelin."""
    for index in range(12):
        with journal.record("cv", template=f"t{index}"):
            _generate(FakeClient("réponse"), step="cv_a", fails_once=False)
    for _ in range(3):
        with journal.record("letter"):
            _generate(FakeClient("réponse"), step="routage", fails_once=False)

    cvs = _rows(db, llm_runs, kind="cv")
    assert len(cvs) == JOURNAL_KEEP_PER_KIND
    # Ce sont bien les DERNIERS : les deux premiers ont disparu.
    assert {run.template for run in cvs} == {f"t{i}" for i in range(2, 12)}
    assert len(_rows(db, llm_runs, kind="letter")) == 3

    kept = {run.run_id for run in _rows(db, llm_runs)}
    assert {x.run_id for x in _rows(db, llm_exchanges)} <= kept
    assert _count(db, llm_exchanges) == len(kept)


# --- 4. mono-poste : une seule base, celle que lit l'administration -----------------


def test_local_mode_writes_where_the_admin_screen_reads(tmp_path, monkeypatch):
    """Sans DATABASE_URL, le journal et l'administration doivent viser le MÊME fichier.

    Les deux le résolvent chacun de leur côté (`get_offers_db` pour l'écriture,
    `get_admin_service` pour la lecture) : s'ils divergeaient, l'onglet resterait vide
    alors que les générations seraient bien enregistrées, et rien ne le dirait.
    """
    from jobtool.api import deps

    local_db = tmp_path / "local.db"
    connect_sqlite(local_db).close()
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("JOBTOOL_AGG_DB", str(local_db))
    deps.get_settings.cache_clear()
    deps.get_engine.cache_clear()
    try:
        journal = DbGenerationJournal(db=deps.get_offers_db(), user_id=LOCAL_USER_ID)
        with journal.record("cv", offer_id="h-a", template="compact"):
            _generate(FakeClient("réponse"), step="cv_a", fails_once=False)

        runs = deps.get_admin_service().llm_runs()
    finally:
        deps.get_settings.cache_clear()
        deps.get_engine.cache_clear()

    assert [(r.kind, r.account, r.calls) for r in runs] == [("cv", "local@jobtool", 1)]


# --- 5. routes d'administration ----------------------------------------------------


@pytest.fixture
def admin_client(db, journal):
    """Une génération enregistrée, le compte local administrateur, un autre compte ordinaire."""
    with journal.record("cv", offer_id="h-a", template="compact"):
        _generate(FakeClient("brouillon", "corrigé"), step="cv_a", fails_once=True)

    with db.connect() as conn:
        ensure_user(conn, OTHER_USER, email="autre@jobtool.fr", name="Autre")
        conn.execute(
            users.update().where(users.c.id == LOCAL_USER_ID).values(is_admin=True)
        )
        conn.commit()

    app.dependency_overrides[get_admin_service] = lambda: AdminService(db)
    app.dependency_overrides[get_current_user_id] = lambda: LOCAL_USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_situates_the_runs_without_any_prompt(admin_client):
    body = admin_client.get("/admin/llm-runs").json()

    assert len(body) == 1
    run = body[0]
    assert run["kind"] == "cv" and run["calls"] == 2 and run["status"] == "ok"
    assert run["account"] == "local@jobtool"
    assert run["offer_id"] == "h-a"
    assert SECRET not in str(body), "la liste ne doit transporter aucun prompt"


def test_detail_returns_every_call_in_order(admin_client):
    run_id = admin_client.get("/admin/llm-runs").json()[0]["run_id"]

    body = admin_client.get(f"/admin/llm-runs/{run_id}").json()

    assert [x["seq"] for x in body["exchanges"]] == [1, 2]
    assert [x["attempt"] for x in body["exchanges"]] == [1, 2]
    assert body["exchanges"][0]["messages"][0]["content"] == SECRET
    assert body["exchanges"][1]["response"] == {"texte": "corrigé"}
    assert body["run"]["run_id"] == run_id


def test_unknown_run_is_404(admin_client):
    assert admin_client.get("/admin/llm-runs/inconnue").status_code == 404


def test_journal_is_refused_to_a_non_admin(admin_client):
    app.dependency_overrides[get_current_user_id] = lambda: OTHER_USER
    run_id = "peu-importe"

    assert admin_client.get("/admin/llm-runs").status_code == 403
    assert admin_client.get(f"/admin/llm-runs/{run_id}").status_code == 403
