"""Stockage des offres, vu d'UN compte : le catalogue commun + l'état de ce compte.

Deux choses distinctes (voir core/db/schema.py) :
  - le CATALOGUE (`offers`, alimenté par les collectes via aggregator/catalogue.py) : le
    contenu des annonces, COMMUN à tous les comptes. Une offre collectée est visible de
    tous, sans que personne ait eu à la « chercher » ;
  - l'ÉTAT d'une offre POUR UN COMPTE (`user_offers`) : favori, statut (masquée,
    postulée…), date de candidature, description corrigée. Une ligne n'existe que si le
    compte a FAIT quelque chose de l'offre ; sans ligne, l'offre est simplement nouvelle.

Rien ne change l'état d'une offre pour un compte sans un geste EXPLICITE de sa part : ni
une collecte, ni une recherche. (Une recherche masquait autrefois les résultats de la
précédente : ce qu'une collecte ramenait disparaissait à la recherche suivante.)

Les offres saisies à la main sont PRIVÉES (`offers.owner_user_id`) : visibles de leur seul
auteur.

`user_id` est un paramètre EXPLICITE, sans valeur par défaut. C'est délibéré : une valeur
par défaut transformerait un oubli d'appelant en fuite silencieuse d'un compte vers un
autre (l'état de l'un lu comme celui de l'autre). Sans défaut, l'oubli est une TypeError.

Le moteur (Postgres en production, SQLite en test et en mono-poste) est choisi en amont :
ce module ne reçoit qu'une `Connection` et n'écrit aucun SQL propre à un dialecte.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, false, func, or_, select, update
from sqlalchemy.engine import Connection, Row
from sqlalchemy.sql import FromClause

from jobtool.aggregator import catalogue
from jobtool.aggregator.dedup import offer_url_key
from jobtool.aggregator.models import Job, JobStatus, Salary
from jobtool.aggregator.normalize import normalize_text, url_native_id
from jobtool.core.db import (
    LOCAL_USER_ID,
    chunked,
    connect_sqlite,
    ensure_user,
    insert_ignore,
    occurrence_urls,
    offer_occurrences,
    offers,
    open_connection,
    user_offers,
)

#: Ré-exports. Ces trois notions sont définies dans core/db parce qu'elles servent AUSSI
#: aux documents, et qu'un domaine n'importe jamais un autre domaine ; les appelants
#: historiques et la suite de tests les nomment ici. Voir core/db/identity.py et engine.py.
__all__ = ["LOCAL_USER_ID", "connect", "ensure_user", "open_connection"]


def _now_iso() -> str:
    """Horodatage courant (UTC, ISO-8601) — sert de date de candidature."""
    return datetime.now(UTC).isoformat()


#: Ouverture de connexion : définie dans core/db, parce que les documents en ont besoin
#: aussi et qu'un domaine n'importe jamais un autre domaine. Ré-exportée ici sous ses noms
#: historiques, que les appelants et la suite de tests nomment beaucoup.
connect = connect_sqlite


# --- Visibilité ----------------------------------------------------------------


def _visible(user_id: uuid.UUID) -> Any:
    """Offres qu'un compte peut voir : le catalogue commun et ses offres privées."""
    return or_(offers.c.owner_user_id.is_(None), offers.c.owner_user_id == user_id)


def _job_from(user_id: uuid.UUID) -> FromClause:
    """Catalogue + état DE CE COMPTE (jointure EXTERNE : sans ligne, l'offre est nouvelle).

    Le compte est dans la CONDITION DE JOINTURE, pas dans un WHERE : un WHERE sur
    `user_offers.user_id` écarterait les offres sans état, c'est-à-dire presque toutes.
    """
    return offers.outerjoin(
        user_offers,
        and_(
            user_offers.c.dedup_hash == offers.c.dedup_hash,
            user_offers.c.user_id == user_id,
        ),
    )


#: Statut et favori EFFECTIFS : l'absence de ligne d'état vaut « nouvelle, pas favorite ».
_STATUS = func.coalesce(user_offers.c.status, JobStatus.new.value)
_FAVORITE = func.coalesce(user_offers.c.favorite, false())


def _ensure_state(conn: Connection, user_id: uuid.UUID, dedup_hash: str) -> bool:
    """Crée la ligne d'état du compte si l'offre lui est visible. False : offre inconnue.

    Les gestes (favori, statut, description) écrivent dans cette ligne ; elle n'existe pas
    tant que le compte n'a rien fait de l'offre.
    """
    visible = conn.execute(
        select(offers.c.dedup_hash).where(
            offers.c.dedup_hash == dedup_hash, _visible(user_id)
        )
    ).first()
    if visible is None:
        return False
    insert_ignore(
        conn,
        user_offers,
        [
            {
                "user_id": user_id,
                "dedup_hash": dedup_hash,
                "status": JobStatus.new.value,
                "favorite": False,
                "applied_at": None,
                "description_override": None,
                "added_at": _now_iso(),
            }
        ],
    )
    return True


# --- Écriture ----------------------------------------------------------------


def upsert_job(
    conn: Connection, user_id: uuid.UUID, job: Job, *, private: bool = False
) -> bool:
    """Range UNE offre au catalogue et dans la vue du compte (cas à un élément de
    `upsert_jobs`, qui porte seul la règle de dédoublonnage)."""
    return upsert_jobs(conn, user_id, [job], private=private)[0]


def _mine(conn: Connection, user_id: uuid.UUID, hashes: set[str]) -> set[str]:
    """Parmi `hashes`, les offres dont ce compte a déjà une ligne d'état."""
    found: set[str] = set()
    for part in chunked(sorted(hashes)):
        stmt = select(user_offers.c.dedup_hash).where(
            user_offers.c.user_id == user_id, user_offers.c.dedup_hash.in_(part)
        )
        found.update(row[0] for row in conn.execute(stmt))
    return found


def upsert_jobs(
    conn: Connection, user_id: uuid.UUID, jobs: list[Job], *, private: bool = False
) -> list[bool]:
    """Range des offres au catalogue ET dans la vue du compte. Un booléen par offre.

    Chemin des offres qu'un compte AJOUTE lui-même (saisie manuelle, reprises) : une
    ligne d'état est créée pour lui. Les collectes, elles, passent par
    `catalogue.upsert_catalogue` et ne créent l'état de personne.

    Le résultat vaut True quand l'offre ENTRE dans la vue du compte à cette occasion.
    Une offre déjà connue du catalogue (vue par une autre source ou un autre compte)
    n'est pas recréée : elle est rattachée à l'offre existante (dédoublonnage).

    `private=True` : offre visible de ce seul compte (saisie manuelle).
    """
    if not jobs:
        return []
    outcomes = catalogue.upsert_catalogue(
        conn, jobs, owner=user_id if private else None
    )
    mine = _mine(conn, user_id, {o.offer_id for o in outcomes})
    rows: list[dict[str, Any]] = []
    results: list[bool] = []
    for job, outcome in zip(jobs, outcomes, strict=True):
        is_new = outcome.offer_id not in mine
        if is_new:
            mine.add(outcome.offer_id)
            rows.append(
                {
                    "user_id": user_id,
                    "dedup_hash": outcome.offer_id,
                    "status": job.status.value,
                    "favorite": bool(job.favorite),
                    "applied_at": job.applied_at,
                    "description_override": None,
                    "added_at": job.fetched_date,
                }
            )
        results.append(is_new)
    insert_ignore(conn, user_offers, rows)
    conn.commit()
    return results


def set_status(
    conn: Connection, user_id: uuid.UUID, dedup_hash: str, status: JobStatus
) -> bool:
    """Met à jour le statut pour ce compte ; True si l'offre existe (et lui est visible).

    Tient `applied_at` synchrone du statut, en une écriture :
      - passage à `applied` -> horodaté maintenant, mais seulement si la date est absente
        (re-marquer « postulé » ne réécrit pas la date de la première candidature) ;
      - tout autre statut -> `applied_at` remis à NULL (l'offre quitte l'historique).
    `hidden_at` date le masquage (statut `seen`) ; toute autre transition l'efface.
    """
    # Une offre fusionnée garde son ancien identifiant comme alias (catalogue.py).
    dedup_hash = catalogue.resolve_offer_id(conn, dedup_hash)
    if not _ensure_state(conn, user_id, dedup_hash):
        conn.commit()
        return False
    where = (
        user_offers.c.user_id == user_id,
        user_offers.c.dedup_hash == dedup_hash,
    )
    if status is JobStatus.applied:
        values: dict[str, Any] = {
            "status": status.value,
            "applied_at": func.coalesce(user_offers.c.applied_at, _now_iso()),
            "hidden_at": None,
        }
    else:
        values = {
            "status": status.value,
            "applied_at": None,
            "hidden_at": _now_iso() if status is JobStatus.seen else None,
        }
    changed = conn.execute(update(user_offers).where(*where).values(values)).rowcount
    conn.commit()
    return changed > 0


def set_favorite(
    conn: Connection, user_id: uuid.UUID, dedup_hash: str, favorite: bool
) -> bool:
    """(Dé)marque une offre comme favorite POUR CE COMPTE. Ne touche jamais au statut."""
    dedup_hash = catalogue.resolve_offer_id(conn, dedup_hash)
    if not _ensure_state(conn, user_id, dedup_hash):
        conn.commit()
        return False
    changed = conn.execute(
        update(user_offers)
        .where(
            user_offers.c.user_id == user_id,
            user_offers.c.dedup_hash == dedup_hash,
        )
        .values(favorite=bool(favorite))
    ).rowcount
    conn.commit()
    return changed > 0


def set_description(
    conn: Connection, user_id: uuid.UUID, dedup_hash: str, description: str
) -> bool:
    """Corrige la description POUR CE COMPTE (la description mutualisée reste intacte).

    Cas d'usage : la source ne renvoie qu'une description partielle, l'utilisateur la
    complète. Écrire dans le catalogue imposerait sa correction à tous les autres comptes.
    """
    dedup_hash = catalogue.resolve_offer_id(conn, dedup_hash)
    if not _ensure_state(conn, user_id, dedup_hash):
        conn.commit()
        return False
    changed = conn.execute(
        update(user_offers)
        .where(
            user_offers.c.user_id == user_id,
            user_offers.c.dedup_hash == dedup_hash,
        )
        .values(description_override=description)
    ).rowcount
    conn.commit()
    return changed > 0


def forget_offer(conn: Connection, user_id: uuid.UUID, dedup_hash: str) -> bool:
    """Efface l'ÉTAT d'un compte sur une offre (elle redevient « nouvelle » pour lui).

    Le catalogue n'est pas touché : l'offre reste visible, simplement sans favori ni
    statut. True si le compte avait un état sur cette offre.
    """
    dedup_hash = catalogue.resolve_offer_id(conn, dedup_hash)
    changed = (
        conn.execute(
            delete(user_offers).where(
                user_offers.c.user_id == user_id,
                user_offers.c.dedup_hash == dedup_hash,
            )
        ).rowcount
        > 0
    )
    conn.commit()
    return changed


# --- Lecture -----------------------------------------------------------------

#: Colonnes lues par `_row_to_job` : le contenu vient du catalogue, l'état du compte (ou
#: ses valeurs par défaut), et la description est celle du compte SI elle a été corrigée.
_JOB_COLUMNS = (
    offers.c.dedup_hash,
    offers.c.id,
    offers.c.source,
    offers.c.native_id,
    offers.c.title,
    offers.c.company,
    offers.c.location,
    offers.c.remote,
    offers.c.contract_type,
    offers.c.salary,
    func.coalesce(user_offers.c.description_override, offers.c.description).label(
        "description"
    ),
    offers.c.url,
    offers.c.posted_date,
    offers.c.fetched_date,
    offers.c.raw,
    offers.c.country,
    offers.c.region,
    offers.c.department,
    offers.c.city,
    offers.c.place_precision,
    offers.c.contract_kind,
    offers.c.work_time,
    offers.c.expired_at,
    offers.c.owner_user_id,
    _STATUS.label("status"),
    _FAVORITE.label("favorite"),
    user_offers.c.applied_at,
    user_offers.c.hidden_at,
)


def _row_to_job(row: Row) -> Job:
    salary = Salary(**row.salary) if row.salary else None
    return Job(
        id=row.id,
        source=row.source,
        native_id=row.native_id,
        title=row.title,
        company=row.company,
        location=row.location,
        remote=row.remote,
        contract_type=row.contract_type,
        salary=salary,
        description=row.description,
        url=row.url,
        posted_date=row.posted_date,
        fetched_date=row.fetched_date,
        dedup_hash=row.dedup_hash,
        status=JobStatus(row.status),
        favorite=bool(row.favorite),
        applied_at=row.applied_at,
        hidden_at=row.hidden_at,
        raw=row.raw or {},
        country=row.country,
        region=row.region,
        department=row.department,
        city=row.city,
        place_precision=row.place_precision or "inconnu",
        contract_kind=row.contract_kind or "inconnu",
        work_time=row.work_time,
        expired_at=row.expired_at,
        private=row.owner_user_id is not None,
    )


def _with_links(conn: Connection, jobs: list[Job]) -> list[Job]:
    """Joint à chaque offre ses liens par source (une requête pour toute la page)."""
    if not jobs:
        return jobs
    links = catalogue.occurrences_for(conn, [j.dedup_hash for j in jobs])
    for job in jobs:
        job.links = [
            {"source": o["source"], "url": o["url"]}
            for o in links.get(job.dedup_hash, [])
        ]
    return jobs


#: Tris autorisés -> critères SQLAlchemy. LISTE BLANCHE : la valeur reçue est une CLÉ de ce
#: dict, jamais un fragment de SQL. Les offres sans date partent EN FIN de liste (le premier
#: critère trie les NULL après) : une date inconnue n'est pas une date récente.
_ORDER_CLAUSES = {
    "fetched_date": (offers.c.fetched_date.desc(),),
    "posted_date": (offers.c.posted_date.is_(None), offers.c.posted_date.desc()),
    "applied_at": (user_offers.c.applied_at.is_(None), user_offers.c.applied_at.desc()),
}

#: Tri par défaut : ordre historique de `list_jobs`.
DEFAULT_ORDER = "fetched_date"


def _order_by(order_by: str):
    """Critères de tri correspondant à une clé de la liste blanche ; lève si inconnue."""
    try:
        return _ORDER_CLAUSES[order_by]
    except KeyError:
        raise ValueError(
            f"tri inconnu : {order_by!r} (connus : {', '.join(sorted(_ORDER_CLAUSES))})"
        ) from None


@dataclass(frozen=True)
class JobQuery:
    """Critères de sélection des offres d'un compte, tels qu'ils partent au SQL.

    Tous les filtres sont appliqués par la BASE, et non après coup en Python : c'est la
    condition pour que la pagination ait un sens. Filtrer après avoir découpé une tranche
    donnerait des pages de tailles fantaisistes ; découper après avoir tout chargé
    n'économiserait ni transfert ni mémoire.
    """

    status: JobStatus | None = None
    #: Recherche : CHAQUE mot doit figurer dans l'intitulé ou l'entreprise (sans casse ni
    #: accents). « data scientist » trouve « Data Scientist (H/F) » et « Senior Data
    #: Scientist », pas une offre qui ne parlerait que de « data ».
    search: str = ""
    keyword: str = ""  # sous-chaîne du titre OU de l'entreprise (filtre d'affinage)
    location: str = ""  # sous-chaîne du lieu (texte de la source)
    source: str | None = None
    favorite: bool | None = None
    #: Borne INFÉRIEURE de publication, au format `YYYY-MM-DD`. Les offres sans date connue
    #: sont écartées : on ne peut pas prouver qu'elles sont récentes.
    #:
    #: Une DATE et non un instant : les sources horodatent dans des fuseaux et des précisions
    #: variables (`…Z`, `…+02:00`, avec ou sans millisecondes), et comparer ces chaînes entre
    #: elles n'a de sens que sur les dix premiers caractères — qui, eux, sont normalisés par
    #: ISO-8601. Le filtre s'exprimant en JOURS, on ne perd aucune précision utile.
    published_after: str | None = None
    #: Départements INSEE (« 75 », « 2A »), sur le lieu NORMALISÉ. Une offre en télétravail
    #: complet passe ce filtre : le lieu ne la contraint pas.
    departments: tuple[str, ...] = ()
    #: Familles de contrat normalisées (contracts.CONTRACT_KINDS).
    contracts: tuple[str, ...] = ()
    #: Pays ISO2 (« FR »), sur le lieu normalisé.
    country: str | None = None
    #: Garder, sous un filtre de lieu, les offres dont le lieu n'est PAS exploitable
    #: (département ou pays inconnus). Écartées par défaut : on ne peut pas prouver
    #: qu'elles sont dans la zone demandée.
    include_unknown_location: bool = False


def _search_terms(text: str) -> list[str]:
    return [t for t in normalize_text(text).split() if t]


def _job_filters(user_id: uuid.UUID, q: JobQuery) -> list[Any]:
    """Conditions SQL correspondant à `q`, pour la liste comme pour le décompte.

    Une seule définition pour les deux : un total calculé avec d'autres critères que la
    page afficherait « 3 sur 120 » sans que rien ne signale l'incohérence.

    `contains(..., autoescape=True)` échappe `%` et `_`, qui sont des JOKERS en SQL : sans
    cela, une recherche sur « 100% » ramènerait tout ce qui commence par « 100 ».

    EXPIRATION : une offre expirée (plus vue par aucune collecte) n'apparaît plus, sauf au
    compte qui y tient (favori, candidature) — c'est pour lui qu'elle a été conservée.
    """
    where: list[Any] = [
        _visible(user_id),
        or_(
            offers.c.expired_at.is_(None),
            user_offers.c.favorite.is_(True),
            user_offers.c.applied_at.is_not(None),
            user_offers.c.status.in_(
                (JobStatus.applied.value, JobStatus.rejected.value)
            ),
        ),
    ]
    if q.status is not None:
        where.append(_STATUS == q.status.value)
    for term in _search_terms(q.search):
        where.append(
            or_(
                func.lower(offers.c.title).contains(term, autoescape=True),
                func.lower(offers.c.company).contains(term, autoescape=True),
                # Sans accents : « ingenieur » trouve « Ingénieur ».
                offers.c.title_key.contains(term, autoescape=True),
                offers.c.company_key.contains(term, autoescape=True),
            )
        )
    if q.keyword.strip():
        motif = q.keyword.strip()
        where.append(
            or_(
                offers.c.title.contains(motif, autoescape=True),
                offers.c.company.contains(motif, autoescape=True),
            )
        )
    if q.location.strip():
        where.append(offers.c.location.contains(q.location.strip(), autoescape=True))
    if q.source:
        where.append(offers.c.source == q.source)
    if q.favorite is not None:
        where.append(_FAVORITE.is_(True) if q.favorite else _FAVORITE.is_(False))
    if q.published_after:
        where.append(offers.c.posted_date.is_not(None))
        where.append(func.substr(offers.c.posted_date, 1, 10) >= q.published_after)
    if q.departments:
        in_zone = [
            offers.c.department.in_(list(q.departments)),
            offers.c.remote == "oui",
        ]
        if q.include_unknown_location:
            in_zone.append(offers.c.department.is_(None))
        where.append(or_(*in_zone))
    if q.country:
        in_country = [offers.c.country == q.country]
        if q.include_unknown_location:
            in_country.append(offers.c.country.is_(None))
        where.append(or_(*in_country))
    if q.contracts:
        where.append(offers.c.contract_kind.in_(list(q.contracts)))
    return where


def list_jobs(
    conn: Connection,
    user_id: uuid.UUID,
    query: JobQuery | None = None,
    order_by: str = DEFAULT_ORDER,
    *,
    limit: int | None = None,
    offset: int = 0,
    with_links: bool = True,
) -> list[Job]:
    """Offres visibles du compte, filtrées et ordonnées par le SQL, éventuellement par
    tranche. Chaque offre porte ses liens par source (`Job.links`), lus en une requête.

    Les critères arrivent par `JobQuery`, et par lui seul.
    """
    q = query or JobQuery()

    stmt = (
        select(*_JOB_COLUMNS)
        .select_from(_job_from(user_id))
        .where(*_job_filters(user_id, q))
        .order_by(*_order_by(order_by))
    )
    if limit is not None:
        # Départager les ex æquo : sans second critère, deux offres publiées à la même
        # seconde peuvent changer de place d'une requête à l'autre — et donc apparaître
        # deux fois, ou jamais, quand on tourne les pages.
        stmt = stmt.order_by(offers.c.dedup_hash).limit(limit).offset(offset)
    jobs = [_row_to_job(row) for row in conn.execute(stmt)]
    return _with_links(conn, jobs) if with_links else jobs


def count_matching_jobs(
    conn: Connection, user_id: uuid.UUID, query: JobQuery | None = None
) -> int:
    """Nombre d'offres correspondant aux mêmes critères que `list_jobs`."""
    stmt = (
        select(func.count())
        .select_from(_job_from(user_id))
        .where(*_job_filters(user_id, query or JobQuery()))
    )
    return int(conn.execute(stmt).scalar() or 0)


def get_job(conn: Connection, user_id: uuid.UUID, dedup_hash: str) -> Job | None:
    """Une offre visible du compte par son identité canonique (ou un ancien identifiant
    fusionné), ou None. Les offres expirées restent consultables par leur identifiant."""
    dedup_hash = catalogue.resolve_offer_id(conn, dedup_hash)
    stmt = (
        select(*_JOB_COLUMNS)
        .select_from(_job_from(user_id))
        .where(offers.c.dedup_hash == dedup_hash, _visible(user_id))
        .limit(1)
    )
    row = conn.execute(stmt).first()
    return _with_links(conn, [_row_to_job(row)])[0] if row is not None else None


def find_by_url(conn: Connection, user_id: uuid.UUID, url: str) -> Job | None:
    """Offre visible du compte portant cette URL (comparée NORMALISÉE), ou None.

    Sert à répondre « cette annonce est déjà là » sur un ajout manuel en doublon : on
    renvoie l'offre existante plutôt qu'un simple refus.
    """
    url_key = url_native_id(url)
    if not url_key:
        return None
    # Toutes les URL connues d'une offre (lien ATS, France Travail, partenaires…) : un
    # lien collé depuis n'importe quelle source retrouve l'offre dédoublonnée.
    identity = offer_url_key(url)
    by_occurrence = (
        select(offer_occurrences.c.offer_id)
        .select_from(
            occurrence_urls.join(
                offer_occurrences,
                and_(
                    offer_occurrences.c.source == occurrence_urls.c.source,
                    offer_occurrences.c.native_id == occurrence_urls.c.native_id,
                ),
            )
        )
        .where(occurrence_urls.c.url_key == identity)
    )
    stmt = (
        select(*_JOB_COLUMNS)
        .select_from(_job_from(user_id))
        .where(
            or_(offers.c.url_key == url_key, offers.c.dedup_hash.in_(by_occurrence)),
            _visible(user_id),
        )
        .order_by(offers.c.dedup_hash)
        .limit(1)
    )
    row = conn.execute(stmt).first()
    return _with_links(conn, [_row_to_job(row)])[0] if row is not None else None


def sources_for(conn: Connection, dedup_hash: str) -> list[str]:
    """Sources où l'offre a été vue (occurrences), triées."""
    stmt = (
        select(offer_occurrences.c.source)
        .where(offer_occurrences.c.offer_id == dedup_hash)
        .distinct()
        .order_by(offer_occurrences.c.source)
    )
    return [row.source for row in conn.execute(stmt)]


def count_jobs(conn: Connection, user_id: uuid.UUID) -> int:
    """Nombre d'offres visibles du compte."""
    return count_matching_jobs(conn, user_id, JobQuery())


# --- Agrégats (comptés PAR LA BASE) ------------------------------------------
#
# Ces deux lectures existent pour ne pas rapatrier tout le catalogue afin d'en tirer
# quelques valeurs.


def distinct_sources(conn: Connection, user_id: uuid.UUID) -> list[str]:
    """Sources distinctes des offres visibles du compte, triées."""
    stmt = (
        select(offers.c.source)
        .select_from(_job_from(user_id))
        .where(*_job_filters(user_id, JobQuery()))
        .distinct()
        .order_by(offers.c.source)
    )
    return [row.source for row in conn.execute(stmt)]


def count_by_status(conn: Connection, user_id: uuid.UUID) -> dict[str, int]:
    """{statut -> nombre d'offres} pour ce compte. Seuls les statuts PRÉSENTS y figurent.

    Une offre sans état compte comme `new`. L'appelant complète les statuts absents.
    """
    status = _STATUS.label("status")
    stmt = (
        select(status, func.count())
        .select_from(_job_from(user_id))
        .where(*_job_filters(user_id, JobQuery()))
        .group_by(status)
    )
    return {row[0]: row[1] for row in conn.execute(stmt)}


# --- Normalisation des offres déjà en base ------------------------------------

#: Ré-export : le recalcul porte sur les occurrences du catalogue (catalogue.py).
renormalize_catalogue = catalogue.renormalize_catalogue
