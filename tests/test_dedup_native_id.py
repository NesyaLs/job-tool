"""Régression dédup : une offre re-agrégée (MÊME source + MÊME native_id) avec une simple
dérive de texte ne doit NI créer un doublon NI revenir à 'new' après avoir été marquée 'seen'.

ROUGE attendu sur le code actuel : `dedup_hash = company+title+location` -> une variation de
titre/lieu change le hash -> `INSERT OR IGNORE` insère une 2e ligne en status=new (l'offre
traitée réapparaît + doublon). Sans réseau ; base SQLite jetable ; réutilise RawJob/normalize/
storage et le chemin d'écriture du front (offers_service.update_status).
"""

from __future__ import annotations

from jobtool.aggregator import storage
from jobtool.aggregator.models import JobStatus, RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.services.offers_service import OffersService

SOURCE = "france_travail"
NATIVE_ID = "FT-DEDUP-1"  # identifiant natif STABLE renvoyé par la source


def _raw(title: str, location: str) -> RawJob:
    """Même offre réelle (source + native_id constants), seul le texte affiché varie."""
    return RawJob(
        source=SOURCE,
        native_id=NATIVE_ID,
        title=title,
        company="Acme",
        location=location,
        description="Offre de test.",
        url="https://example.fr/ft/FT-DEDUP-1",
    )


def _rows_for_offer(conn):
    """Lignes d'état correspondant à CETTE offre, identifiée par (source, native_id)."""
    from sqlalchemy import select

    from jobtool.core.db import offers, user_offers

    stmt = (
        select(user_offers.c.status)
        .select_from(
            offers.join(user_offers, user_offers.c.dedup_hash == offers.c.dedup_hash)
        )
        .where(offers.c.source == SOURCE, offers.c.native_id == NATIVE_ID)
    )
    return conn.execute(stmt).fetchall()


def test_reaggregation_with_text_drift_keeps_single_seen_row(tmp_db):
    # 1) Agrégation initiale (chemin réel : normalize -> upsert) -> status new.
    conn = storage.connect(tmp_db)
    job1 = raw_to_job(_raw("Dev Python", "Paris"))
    assert storage.upsert_job(conn, storage.LOCAL_USER_ID, job1) is True, (
        "1re collecte : nouvelle offre"
    )
    conn.close()

    # 2) L'utilisateur la marque 'seen' (MÊME chemin que le front : offers_service.set_status).
    assert OffersService(tmp_db).update_status(job1.dedup_hash, JobStatus.seen) is True

    # 3) Ré-agrégation de LA MÊME offre (même source + native_id) avec dérive de texte.
    conn = storage.connect(tmp_db)
    storage.upsert_job(
        conn, storage.LOCAL_USER_ID, raw_to_job(_raw("Dev Python (H/F)", "Paris 15e"))
    )
    rows = _rows_for_offer(conn)
    conn.close()

    # 4) Une SEULE ligne pour cette offre, et statut resté 'seen' (pas revenu à 'new').
    assert len(rows) == 1, (
        f"doublon : la dérive de texte a ré-inséré l'offre ({len(rows)} lignes)"
    )
    assert rows[0].status == JobStatus.seen.value, (
        f"statut non préservé après ré-agrégation : {rows[0]['status']!r}"
    )
