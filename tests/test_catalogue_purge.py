"""Expiration du catalogue et verrou des collectes.

La purge est la seule opération IRRÉVERSIBLE du catalogue : elle est testée par ce
qu'elle ÉPARGNE autant que par ce qu'elle retire.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from jobtool.aggregator import catalogue, runs, storage
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.core.db import documents, ensure_user, offer_occurrences, offers

NOW = datetime.now(UTC)
CUTOFF = (NOW - timedelta(days=30)).isoformat()
OLD = (NOW - timedelta(days=45)).isoformat()


@pytest.fixture
def base(tmp_db):
    conn = storage.connect(tmp_db)
    user = uuid.uuid4()
    ensure_user(conn, user, email="moi@exemple.fr", name="moi")
    conn.commit()
    yield conn, user
    conn.close()


def _collect(conn, native_id, *, source="adzuna", seen=OLD):
    job = raw_to_job(
        RawJob(
            source=source,
            native_id=native_id,
            title=f"Offre {native_id}",
            company="ACME",
            location="Paris",
            url=f"https://exemple.fr/{native_id}",
        ),
        fetched_date=seen,
    )
    (outcome,) = catalogue.upsert_catalogue(conn, [job])
    conn.commit()
    return outcome.offer_id


def _ids(conn):
    return {r[0] for r in conn.execute(select(offers.c.dedup_hash))}


def _purge(conn, healthy=frozenset({"adzuna"})):
    result = catalogue.purge_expired(conn, cutoff=CUTOFF, healthy_sources=set(healthy))
    conn.commit()
    return result


def test_unseen_offer_of_a_healthy_source_is_removed_everywhere(base):
    conn, _ = base
    old = _collect(conn, "vieille")
    fresh = _collect(conn, "fraiche", seen=NOW.isoformat())
    assert _purge(conn).deleted == 1
    assert _ids(conn) == {fresh}
    left = conn.execute(select(offer_occurrences.c.offer_id)).all()
    assert [r[0] for r in left] == [fresh], "ses occurrences partent avec elle"
    assert old not in _ids(conn)


def test_nothing_expires_when_the_source_was_not_collected_successfully(base):
    conn, _ = base
    _collect(conn, "vieille")
    assert _purge(conn, healthy=set()).deleted == 0, (
        "ne plus voir l'offre d'une source en panne ne prouve pas qu'elle a disparu"
    )


def test_offers_a_user_cares_about_are_kept_and_flagged(base):
    conn, user = base
    fav = _collect(conn, "favorite")
    applied = _collect(conn, "postulee")
    edited = _collect(conn, "corrigee")
    doc = _collect(conn, "document")
    hidden = _collect(conn, "masquee")
    storage.set_favorite(conn, user, fav, True)
    storage.set_status(conn, user, applied, JobStatus.applied)
    storage.set_description(conn, user, edited, "ma version")
    storage.set_status(conn, user, hidden, JobStatus.seen)
    conn.execute(
        documents.insert().values(
            doc_id="d",
            user_id=user,
            offer_id=doc,
            kind="cv",
            template="t",
            render_input={},
            original_texts={},
            overrides={},
            created_at="x",
            updated_at="x",
        )
    )
    conn.commit()

    result = _purge(conn)
    assert result.deleted == 1, "seule l'offre MASQUÉE part : masquer, c'est la rejeter"
    assert result.kept_expired == 4
    assert _ids(conn) == {fav, applied, edited, doc}
    flagged = conn.execute(
        select(offers.c.dedup_hash).where(offers.c.expired_at.is_not(None))
    ).all()
    assert {r[0] for r in flagged} == {fav, applied, edited, doc}

    # Gardée pour ce compte, invisible aux autres : elle n'est plus au catalogue vivant.
    other = uuid.uuid4()
    ensure_user(conn, other, email="autre@exemple.fr", name="autre")
    conn.commit()
    assert storage.count_jobs(conn, other) == 0
    assert storage.get_job(conn, user, fav).expired_at is not None
    assert fav in {j.dedup_hash for j in storage.list_jobs(conn, user)}


def test_a_new_sighting_revives_an_expired_offer(base):
    conn, user = base
    fav = _collect(conn, "favorite")
    storage.set_favorite(conn, user, fav, True)
    _purge(conn)
    _collect(conn, "favorite", seen=NOW.isoformat())
    expired = conn.execute(
        select(offers.c.expired_at).where(offers.c.dedup_hash == fav)
    ).scalar()
    assert expired is None


def test_private_manual_offers_never_expire(base):
    conn, user = base
    job = raw_to_job(
        RawJob(source="manual", native_id="m", title="Perso", url="https://x.fr/m"),
        fetched_date=OLD,
    )
    storage.upsert_jobs(conn, user, [job], private=True)
    assert _purge(conn, healthy={"adzuna", "manual"}).deleted == 0


# --- Verrou des collectes ---------------------------------------------------------------
def test_only_one_collection_at_a_time_and_stale_ones_are_released(base):
    conn, _ = base
    first = runs.start_run(conn, trigger="cli", sources=["adzuna"])
    with pytest.raises(runs.CollectionBusy) as busy:
        runs.start_run(conn, trigger="api", sources=["adzuna"])
    assert busy.value.run_id == first

    # Processus tué : la collecte reste « en cours »… jusqu'à être déclarée interrompue.
    conn.execute(
        update(runs.collection_runs)
        .where(runs.collection_runs.c.id == first)
        .values(started_at=(NOW - timedelta(hours=3)).isoformat())
    )
    conn.commit()
    second = runs.start_run(conn, trigger="api", sources=["adzuna"])
    assert runs.get_run(conn, first).status == "failed"
    assert runs.get_run(conn, second).status == "running"


def test_cooldown_counts_from_the_previous_start(base):
    conn, _ = base
    first = runs.start_run(conn, trigger="api", sources=["adzuna"])
    runs.finish_run(conn, first, status="done")
    with pytest.raises(runs.CollectionTooSoon) as soon:
        runs.start_run(
            conn, trigger="api", sources=["adzuna"], cooldown=timedelta(minutes=15)
        )
    assert 0 < soon.value.retry_after <= 15 * 60 + 1
    # Sans délai (CLI), la collecte est acceptée.
    assert runs.start_run(conn, trigger="cli", sources=["adzuna"])


def test_healthy_sources_require_a_successful_fetch(base):
    conn, _ = base
    run = runs.start_run(conn, trigger="cli", sources=["adzuna", "workday"])
    runs.finish_run(
        conn,
        run,
        status="done",
        sources={
            "adzuna": {"status": "done", "fetched": 12, "errors": []},
            "workday": {"status": "done", "fetched": 0, "errors": ["HTTP 500"]},
        },
    )
    assert runs.healthy_sources_since(conn, CUTOFF) == {"adzuna"}
