"""Couche SERVICE (orchestration) du navigateur d'offres — n'importe aucune couche d'interface.

Frontière d'architecture : c'est le SEUL endroit qui parle à `aggregator.storage` et aux
modèles existants. Il renvoie des modèles Pydantic `Job` (ou des DTO simples) et peut être
importé/testé SANS navigateur. La présentation (UI) ne fait JAMAIS de SQL ni d'accès direct
au stockage : elle passe par ce service.

Répartition des filtres : AUCUNE ne s'applique ici. Tous les critères — statut, mot-clé,
lieu, source, favori, fraîcheur — partent au SQL via `storage.JobQuery`, et les décomptes
sont agrégés par la base. Ce service traduit (« les 7 derniers jours » devient une date) et
orchestre ; il ne filtre ni ne compte en mémoire.

Testabilité : le service REÇOIT le chemin de la base (jamais en dur) -> un test pointe une
base de fixture. `default_db_path()` ne sert qu'au câblage de l'app réelle.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from jobtool.aggregator import storage
from jobtool.aggregator.matching import MatchCriteria, MatchResult, rank_jobs, score_job
from jobtool.aggregator.models import Job, JobStatus
from jobtool.settings import AggregatorSettings

#: Tris exposés aux couches d'interface. Reflète la liste blanche de `storage._ORDER_CLAUSES`
#: (le SQL, lui, vit uniquement dans storage.py). Typé en Literal pour que l'API le valide
#: elle-même (422 sur une valeur hors liste) sans écrire de validation à la main.
OfferOrder = Literal["fetched_date", "posted_date", "applied_at"]

#: Ordres de la liste NOTÉE (`list_ranked`) : par score, ou par l'un des tris SQL.
RankedOrder = Literal["score", "posted_date", "applied_at"]

#: Source des offres saisies à la main (par opposition aux offres collectées par l'agrégateur).
MANUAL_SOURCE = "manual"


def default_db_path() -> Path:
    """Chemin par défaut de la base, centralisé dans AggregatorSettings.

    Même convention que le CLI : variable d'env `JOBTOOL_AGG_DB`, défaut
    `data/aggregator/jobs.db`. Sert au CÂBLAGE de l'app réelle uniquement ; les tests
    passent un chemin de fixture explicite au service (jamais ce défaut).
    """
    return AggregatorSettings().db_path


@dataclass(frozen=True)
class OffersFilter:
    """Critères d'affichage du navigateur (tous optionnels)."""

    status: JobStatus | None = None
    search: str = ""  # recherche : chaque mot dans l'intitulé ou l'entreprise
    keyword: str = ""  # sous-chaîne sur titre OU entreprise (affinage)
    source: str | None = None  # filtre par source exacte (ex. "france_travail")
    location: str = ""  # sous-chaîne sur le lieu (insensible à la casse)
    favorite: bool | None = (
        None  # None = indifférent ; True/False = ne garde que ceux-ci
    )
    # Fraîcheur : PUBLIÉE dans les N derniers jours (posted_date), cohérent avec le tri
    # `posted_date`. None = aucun filtre d'âge (« Toutes »).
    max_age_days: int | None = None
    # Ordre de la liste. Délégué au SQL (storage) et NON au front : trier côté client ne
    # porterait que sur les offres déjà chargées. Défaut = ordre historique.
    order: OfferOrder = "fetched_date"
    # Lieu et contrat NORMALISÉS (voir storage.JobQuery).
    departments: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    country: str | None = None
    include_unknown_location: bool = False


@dataclass(frozen=True)
class OffersPage:
    """Une tranche d'offres et le total correspondant AUX MÊMES critères.

    `total` porte sur l'ensemble filtré, pas sur la tranche : c'est ce qui permet à un
    appelant de dire « 15 sur 369 » et de savoir s'il existe une page suivante.
    """

    items: list[Job]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True)
class RankedPage:
    """Une tranche d'offres NOTÉES et le total correspondant aux mêmes filtres."""

    items: list[MatchResult]
    total: int


@dataclass(frozen=True)
class ManualOfferResult:
    """Issue d'un ajout manuel : l'offre concernée, et si elle vient d'être créée.

    `created=False` signale un doublon (lien déjà connu) : `job` est alors l'offre DÉJÀ
    en base, pour que l'appelant puisse y renvoyer l'utilisateur plutôt que de perdre le geste.
    """

    job: Job
    created: bool


class OffersService:
    """Orchestration de lecture/écriture des offres, par-dessus `aggregator.storage`."""

    def __init__(
        self,
        db: str | Path | Engine,
        user_id: uuid.UUID = storage.LOCAL_USER_ID,
    ):
        """`db` : un moteur (Postgres) ou un chemin de fichier SQLite (mono-poste, tests).

        `user_id` par défaut = le compte du poste local. Ce défaut est FERMÉ, pas ouvert :
        sur la base partagée, ce compte n'existe pas et les requêtes ne renvoient rien —
        un oubli de câblage donne une liste vide, jamais les données d'autrui. L'API, elle,
        passe toujours le compte authentifié.
        """
        self._db = db
        self._user_id = user_id

    @contextmanager
    def _conn(self) -> Iterator[Connection]:
        """Connexion au moteur fourni, ou au fichier SQLite local, refermée à coup sûr.

        Un contextmanager plutôt qu'un `try/finally` par méthode : la fermeture était
        recopiée dix fois, et une seule omission laisse fuir une connexion du pool.
        """
        conn = storage.open_connection(self._db)
        try:
            yield conn
        finally:
            conn.close()

    # --- Lectures -----------------------------------------------------------

    def list_offers(self, flt: OffersFilter | None = None) -> list[Job]:
        """Toutes les offres correspondant au filtre, dans l'ordre demandé.

        Sans pagination : à réserver aux usages qui ont réellement besoin de l'ensemble.
        L'écran, lui, passe par `list_page`.
        """
        flt = flt or OffersFilter()
        with self._conn() as conn:
            return storage.list_jobs(
                conn, self._user_id, order_by=flt.order, query=self._query(flt)
            )

    def list_page(
        self, flt: OffersFilter | None = None, *, limit: int, offset: int = 0
    ) -> OffersPage:
        """Une TRANCHE d'offres, plus le total correspondant aux mêmes critères.

        Le total est indispensable à l'appelant : sans lui, une interface ne peut ni dire
        combien d'offres existent, ni savoir s'il reste une page à afficher.

        Deux requêtes (la tranche, puis le décompte) et non une : compter en ramenant tout
        annulerait précisément ce que la pagination cherche à éviter.
        """
        flt = flt or OffersFilter()
        query = self._query(flt)
        with self._conn() as conn:
            items = storage.list_jobs(
                conn,
                self._user_id,
                order_by=flt.order,
                query=query,
                limit=max(0, limit),
                offset=max(0, offset),
            )
            total = storage.count_matching_jobs(conn, self._user_id, query)
        return OffersPage(items=items, total=total, limit=limit, offset=offset)

    def list_ranked(
        self,
        flt: OffersFilter,
        criteria: MatchCriteria,
        *,
        order: RankedOrder = "score",
        limit: int,
        offset: int = 0,
        now: datetime | None = None,
    ) -> RankedPage:
        """Offres FILTRÉES par la base, puis NOTÉES par `aggregator.matching`.

        Les filtres sont durs (ceux de `list_offers`) ; `criteria` ne fait que noter. Le
        score ne se calcule pas en SQL sans dupliquer la formule : l'ensemble filtré est
        rapatrié en UNE requête, noté en mémoire, puis découpé — comme la pile
        « Découvrir », mais sans seuil minimal : une liste ne cache rien.

        `order="score"` : score décroissant (ordre total et stable de `rank_jobs`) ;
        sinon l'ordre SQL demandé est conservé et chaque offre porte simplement son score.
        """
        now = now or datetime.now(UTC)
        sql_order: OfferOrder = "posted_date" if order == "score" else order
        jobs = self.list_offers(replace(flt, order=sql_order))
        if order == "score":
            results = rank_jobs(jobs, criteria, now=now, min_percent=0)
        else:
            results = [score_job(job, criteria, now=now) for job in jobs]
        start = max(0, offset)
        return RankedPage(items=results[start : start + max(0, limit)], total=len(results))

    def _query(self, flt: OffersFilter) -> storage.JobQuery:
        """Traduit le filtre d'affichage en critères SQL.

        La fraîcheur est convertie ICI en borne de date absolue : le stockage compare des
        dates, il n'a pas à savoir ce qu'est « les 7 derniers jours ».
        """
        published_after = None
        if flt.max_age_days is not None:
            jour = (datetime.now(UTC) - timedelta(days=flt.max_age_days)).date()
            published_after = jour.isoformat()  # YYYY-MM-DD
        return storage.JobQuery(
            status=flt.status,
            search=flt.search,
            departments=flt.departments,
            contracts=flt.contracts,
            country=flt.country,
            include_unknown_location=flt.include_unknown_location,
            keyword=flt.keyword,
            location=flt.location,
            source=flt.source,
            favorite=flt.favorite,
            published_after=published_after,
        )

    def get_offer(self, dedup_hash: str) -> Job | None:
        """Une offre par son identité canonique (dedup_hash), ou None."""
        with self._conn() as conn:
            return storage.get_job(conn, self._user_id, dedup_hash)

    def available_sources(self) -> list[str]:
        """Sources distinctes présentes en base (pour alimenter un filtre)."""
        with self._conn() as conn:
            return storage.distinct_sources(conn, self._user_id)

    def counts_by_status(self) -> dict[str, int]:
        """Compte d'offres par statut (TOUS les statuts, à 0 si aucune offre).

        La base ne renvoie que les statuts présents ; les zéros sont ajoutés ici, parce
        qu'ils relèvent de l'affichage (un onglet « Rejetées (0) » doit rester visible).
        """
        with self._conn() as conn:
            present = storage.count_by_status(conn, self._user_id)
        return {s.value: present.get(s.value, 0) for s in JobStatus}

    # --- Création manuelle --------------------------------------------------

    def create_manual_offer(
        self,
        *,
        titre: str,
        url: str,
        description: str = "",
        entreprise: str = "",
        lieu: str = "",
    ) -> ManualOfferResult:
        """Enregistre une offre saisie à la main ; renvoie l'offre et si elle a été créée.

        Passe par les MÊMES points d'entrée que l'agrégateur (`raw_to_job` + `upsert_job`),
        donc l'offre obtenue est en tout point une offre ordinaire : statuts, favori,
        description éditable, tri et filtres s'y appliquent sans cas particulier.

        `titre` et `url` sont obligatoires (une offre sans lien serait perdue) ; la validation
        de forme appartient à la couche d'interface. `native_id` dérive de l'URL normalisée :
        c'est le seul identifiant stable dont dispose une offre sans source. `posted_date` est
        fixée à MAINTENANT (l'utilisateur vient de voir l'annonce).

        `created=False` -> le lien est déjà connu : on renvoie l'offre EXISTANTE, sans rien
        écraser (la saisie ne doit pas effacer une offre déjà suivie).
        """
        from jobtool.aggregator.models import RawJob
        from jobtool.aggregator.normalize import raw_to_job, url_native_id

        now = datetime.now(UTC).isoformat()
        job = raw_to_job(
            RawJob(
                source=MANUAL_SOURCE,
                # Offre PRIVÉE : son identifiant est propre au compte, pour que deux
                # comptes qui saisissent le même lien aient chacun la leur.
                native_id=f"{self._user_id.hex}:{url_native_id(url)}",
                title=titre,
                company=entreprise,
                location=lieu,
                description=description,
                url=url,
                posted_date=now,
            ),
            fetched_date=now,
        )

        with self._conn() as conn:
            # Lien déjà connu (du catalogue, sous n'importe laquelle de ses URL, ou de
            # ses propres saisies) : on renvoie l'offre existante, sans rien créer.
            existing = storage.find_by_url(conn, self._user_id, url)
            if existing is not None:
                return ManualOfferResult(job=existing, created=False)
            storage.upsert_job(conn, self._user_id, job, private=True)
            created = storage.find_by_url(conn, self._user_id, url) or job
        return ManualOfferResult(job=created, created=True)

    # --- Écriture -----------------------------------------------------------

    def update_status(self, dedup_hash: str, status: JobStatus) -> bool:
        """Change le statut d'une offre (persisté). True si une ligne a été modifiée."""
        with self._conn() as conn:
            return storage.set_status(conn, self._user_id, dedup_hash, status)

    def set_favorite(self, dedup_hash: str, favorite: bool = True) -> bool:
        """(Dé)marque une offre comme favorite (persisté). True si une ligne a été modifiée.

        Drapeau INDÉPENDANT du statut (new/seen/applied/rejected restent inchangés). La
        future page « Postuler » ne listera que les offres dont ce drapeau est True.
        """
        with self._conn() as conn:
            return storage.set_favorite(conn, self._user_id, dedup_hash, favorite)

    def is_favorite(self, dedup_hash: str) -> bool:
        """True si l'offre est marquée favorite (False si non favorite OU introuvable)."""
        offer = self.get_offer(dedup_hash)
        return bool(offer and offer.favorite)

    def update_description(self, dedup_hash: str, description: str) -> bool:
        """Remplace la description d'une offre (persisté). True si une ligne a été modifiée.

        Permet à l'utilisateur de compléter une description partielle renvoyée par la source.
        Ne touche ni au statut ni au favori.
        """
        with self._conn() as conn:
            return storage.set_description(conn, self._user_id, dedup_hash, description)

    # Il n'y a plus de filtrage Python : `_matches` et `_within_days` ont été supprimés.
    # Deux implémentations de la même règle — l'une en SQL, l'autre en mémoire — finissent
    # toujours par diverger, et c'est la pagination qui aurait révélé l'écart le plus tard :
    # une page filtrée après découpage aurait affiché « 15 résultats » dont 3 visibles.
