"""Preuve SANS clé/navigateur de la couche service du navigateur d'offres.

Seed d'une base de FIXTURE tmp (via le vrai storage), puis on exerce OffersService :
lecture, filtres (statut, mot-clé, source, fraîcheur), maj de statut puis relecture qui
CONFIRME la persistance. Aucune couche d'interface requise.
"""

from __future__ import annotations

import pytest

from jobtool.aggregator.models import JobStatus
from jobtool.services.offers_service import OffersFilter, OffersService


@pytest.fixture
def service(seed_offers, tmp_db):
    seed_offers(tmp_db)
    return OffersService(tmp_db)  # le service REÇOIT le chemin de fixture (injectable)


def test_reads_and_counts(service):
    assert len(service.list_offers()) == 4, "4 offres lues"
    counts = service.counts_by_status()
    assert counts["new"] == 2 and counts["seen"] == 1 and counts["applied"] == 1, (
        f"comptes par statut : {counts}"
    )
    assert service.available_sources() == ["france_travail", "jobboard_x"], (
        "sources distinctes listées"
    )


def test_filter_by_status(service):
    new_offers = service.list_offers(OffersFilter(status=JobStatus.new))
    assert len(new_offers) == 2 and all(
        o.status is JobStatus.new for o in new_offers
    ), "filtre statut=new -> 2 offres"


def test_filter_by_keyword(service):
    kw_data = service.list_offers(OffersFilter(keyword="data"))
    assert {o.title for o in kw_data} == {"Data Scientist", "Data Engineer"}, (
        "mot-clé 'data' -> match sur le titre"
    )
    kw_company = service.list_offers(OffersFilter(keyword="lcl"))
    assert len(kw_company) == 1 and kw_company[0].company == "LCL", (
        "mot-clé 'lcl' -> match entreprise (insensible casse)"
    )


def test_filter_by_source(service):
    src = service.list_offers(OffersFilter(source="jobboard_x"))
    assert len(src) == 1 and src[0].source == "jobboard_x", (
        "filtre source=jobboard_x -> 1 offre"
    )


def test_freshness_on_offers_without_posted_date(service):
    """CHANGEMENT lot C0 : la fraîcheur compare `posted_date`, plus `fetched_date`.

    Les offres de cette fixture n'ont aucune date de publication : elles sont donc TOUTES
    écartées par un filtre d'âge (on ne peut pas prouver qu'elles sont récentes), alors
    qu'auparavant la comparaison sur `fetched_date` en gardait 3. Voir
    `test_freshness_filters_on_posted_date` pour le comportement sur des offres datées.
    """
    assert service.list_offers(OffersFilter(max_age_days=30)) == []
    assert len(service.list_offers()) == 4, "sans filtre d'âge, elles restent toutes là"


def test_filter_by_location_and_favorite(service):
    paris = service.list_offers(OffersFilter(location="paris"))
    assert {o.location for o in paris} == {"Paris"}, (
        "filtre lieu (sous-chaîne, insensible casse)"
    )

    assert service.list_offers(OffersFilter(favorite=True)) == [], (
        "aucun favori au départ"
    )
    target = service.list_offers()[0]
    service.set_favorite(target.dedup_hash, True)
    favs = service.list_offers(OffersFilter(favorite=True))
    assert [o.dedup_hash for o in favs] == [target.dedup_hash], (
        "favorite=True -> 1 offre"
    )
    assert len(service.list_offers(OffersFilter(favorite=False))) == 3, (
        "favorite=False -> 3"
    )


def test_update_description_persists(service):
    target = service.list_offers()[0]
    assert service.update_description(
        target.dedup_hash, "Description complétée à la main"
    ), "update_description -> True (ligne modifiée)"
    reread = service.get_offer(target.dedup_hash)
    assert (
        reread is not None and reread.description == "Description complétée à la main"
    )
    # N'altère ni le statut ni le favori.
    assert reread.status is target.status and reread.favorite is False
    assert service.update_description("inconnu", "x") is False, "id inconnu -> False"


# --- Tri par date de publication (lot B) ------------------------------------


@pytest.fixture
def dated_service(seed_dated_offers, tmp_db):
    """Service sur une base dont les offres ont des dates de PUBLICATION distinctes."""
    seed_dated_offers(tmp_db)
    return OffersService(tmp_db)


def test_default_order_is_unchanged(dated_service):
    """Sans `order`, on garde l'ordre historique : dernières RÉCUPÉRÉES d'abord."""
    titles = [o.title for o in dated_service.list_offers()]
    assert titles == [
        "Sans date de publication",
        "Publiée il y a un an",
        "Publiée le mois dernier",
        "Publiée hier",
    ], "ordre par fetched_date DESC (la fixture le fait diverger du posted_date)"


def test_order_by_posted_date_desc(dated_service):
    """order='posted_date' : les plus récemment PUBLIÉES d'abord (tri SQL, pas JS)."""
    titles = [
        o.title for o in dated_service.list_offers(OffersFilter(order="posted_date"))
    ]
    assert titles[:3] == [
        "Publiée hier",
        "Publiée le mois dernier",
        "Publiée il y a un an",
    ]


def test_order_by_posted_date_puts_undated_last(dated_service):
    """Une offre sans date de publication n'est pas « récente » : elle finit en queue."""
    jobs = dated_service.list_offers(OffersFilter(order="posted_date"))
    assert jobs[-1].title == "Sans date de publication"
    assert jobs[-1].posted_date is None


def test_order_combines_with_filters(dated_service):
    """Le tri s'applique APRÈS filtrage : filtre et tri coexistent (aucun ne mange l'autre)."""
    jobs = dated_service.list_offers(
        OffersFilter(order="posted_date", keyword="publiée")
    )
    assert [o.title for o in jobs] == [
        "Publiée hier",
        "Publiée le mois dernier",
        "Publiée il y a un an",
    ], "le mot-clé écarte l'offre sans date, le tri ordonne le reste"


# --- Filtre de fraîcheur (lot C0) : porte sur la PUBLICATION --------------


def test_freshness_filters_on_posted_date(dated_service):
    """max_age_days compare la date de PUBLICATION (cohérent avec le tri du lot B)."""
    recent = dated_service.list_offers(OffersFilter(max_age_days=7))
    assert [o.title for o in recent] == ["Publiée hier"], "seule l'offre d'hier a <7 j"
    assert len(dated_service.list_offers(OffersFilter(max_age_days=60))) == 2
    assert len(dated_service.list_offers(OffersFilter(max_age_days=1000))) == 3


def test_freshness_excludes_undated_offers(dated_service):
    """Sans date de publication, on ne peut pas PROUVER qu'une offre est récente : exclue."""
    for days in (1, 7, 30, 100000):
        titles = [
            o.title for o in dated_service.list_offers(OffersFilter(max_age_days=days))
        ]
        assert "Sans date de publication" not in titles


def test_no_freshness_filter_keeps_everything(dated_service):
    """`max_age_days=None` (option « Toutes ») ne retire rien, offres sans date comprises."""
    assert len(dated_service.list_offers()) == 4


def test_freshness_and_order_stack(dated_service):
    """Filtre ET tri s'empilent : le filtre restreint, le tri ordonne l'intérieur."""
    jobs = dated_service.list_offers(OffersFilter(max_age_days=60, order="posted_date"))
    assert [o.title for o in jobs] == ["Publiée hier", "Publiée le mois dernier"], (
        "2 offres retenues par le filtre, ordonnées par publication décroissante"
    )


def test_storage_rejects_unknown_order(tmp_db):
    """Liste blanche dans storage : un `order_by` hors liste lève, jamais d'injection SQL."""
    from jobtool.aggregator import storage

    conn = storage.connect(tmp_db)
    try:
        with pytest.raises(ValueError, match="tri inconnu"):
            storage.list_jobs(
                conn,
                storage.LOCAL_USER_ID,
                None,
                order_by="posted_date; DROP TABLE jobs",
            )
    finally:
        conn.close()


def test_update_status_persists(service):
    target = service.list_offers(OffersFilter(status=JobStatus.new))[0]
    assert service.update_status(target.dedup_hash, JobStatus.applied), (
        "update_status renvoie True (ligne modifiée)"
    )
    # Relecture via une NOUVELLE connexion (le service en rouvre une) -> persistance réelle.
    reread = service.get_offer(target.dedup_hash)
    assert reread is not None and reread.status is JobStatus.applied, (
        "relecture : statut 'applied' PERSISTÉ"
    )
    assert len(service.list_offers(OffersFilter(status=JobStatus.new))) == 1, (
        "après maj : 1 offre 'new'"
    )
    assert len(service.list_offers(OffersFilter(status=JobStatus.applied))) == 2, (
        "après maj : 2 offres 'applied'"
    )
