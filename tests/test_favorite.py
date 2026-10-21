"""Preuve du drapeau `favorite` : indépendant du statut + migration douce des bases existantes.

Couvre : marquer un favori, le retirer (persistance réelle via relecture sur une NOUVELLE
connexion), et le fait qu'une base créée AVANT l'ajout de la colonne (legacy) prend
`favorite=false` sans rien casser. Aucune couche d'interface requise.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator.models import JobStatus
from jobtool.services.offers_service import OffersFilter, OffersService

# Schéma LEGACY (sans colonne `favorite`) : reproduit une jobs.db d'avant la migration.
_LEGACY_SCHEMA = """
CREATE TABLE jobs (
    dedup_hash   TEXT PRIMARY KEY,
    id           TEXT NOT NULL,
    source       TEXT NOT NULL,
    native_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    company      TEXT NOT NULL,
    location     TEXT NOT NULL,
    remote       TEXT NOT NULL,
    contract_type TEXT NOT NULL,
    salary_json  TEXT,
    description  TEXT NOT NULL,
    url          TEXT NOT NULL,
    posted_date  TEXT,
    fetched_date TEXT NOT NULL,
    status       TEXT NOT NULL,
    raw_json     TEXT NOT NULL
);
"""


@pytest.fixture
def service(seed_offers, tmp_db):
    seed_offers(tmp_db)
    return OffersService(tmp_db)  # le service REÇOIT le chemin de fixture (injectable)


def test_set_favorite_then_unset(service):
    target = service.list_offers()[0]
    assert target.favorite is False, (
        "défaut : une offre fraîchement insérée n'est pas favorite"
    )

    # Marquer favori.
    assert service.set_favorite(target.dedup_hash), (
        "set_favorite -> True (ligne modifiée)"
    )
    assert service.is_favorite(target.dedup_hash), "relecture : favori PERSISTÉ"
    reread = service.get_offer(target.dedup_hash)
    assert reread is not None and reread.favorite is True

    # Retirer le favori.
    assert service.set_favorite(target.dedup_hash, False), (
        "retrait -> True (ligne modifiée)"
    )
    assert not service.is_favorite(target.dedup_hash), "relecture : favori RETIRÉ"


def test_favorite_is_independent_of_status(service):
    """Basculer le favori ne change PAS le statut, et inversement."""
    target = service.list_offers(OffersFilter(status=JobStatus.new))[0]

    service.set_favorite(target.dedup_hash, True)
    after = service.get_offer(target.dedup_hash)
    assert after is not None and after.favorite is True
    assert after.status is JobStatus.new, "le statut reste 'new' après mise en favori"

    service.update_status(target.dedup_hash, JobStatus.applied)
    after = service.get_offer(target.dedup_hash)
    assert after is not None and after.status is JobStatus.applied
    assert after.favorite is True, "changer le statut ne touche pas le drapeau favori"


# Le test de « migration douce » d'une base legacy a été retiré avec le code qu'il
# couvrait : le schéma appartient désormais à Alembic, et la reprise d'une base locale
# est le rôle de scripts/import_local_to_postgres.py.
