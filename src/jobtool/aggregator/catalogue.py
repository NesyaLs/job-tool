"""Catalogue d'offres : occurrences par source, offres canoniques, dédoublonnage, fusions.

C'est ici — et seulement ici — que l'on écrit le CONTENU des offres. L'état propre à un
compte (favori, statut…) vit dans `storage.py`.

MODÈLE :
  - `offer_occurrences` : ce que CHAQUE source a dit d'une annonce (contenu, liens, champs
    normalisés), clé (source, identifiant natif). C'est la donnée de référence ;
  - `offers` : l'offre CANONIQUE, recalculée à partir de ses occurrences selon la priorité
    des sources (aggregator/dedup.best_fields). Rien n'est supprimé pour dédoublonner :
    une occurrence est RATTACHÉE à une offre existante ;
  - `occurrence_urls` : toutes les identités d'URL de chaque occurrence, pour le niveau 2 ;
  - `offer_aliases` : l'ancien identifiant d'une offre fusionnée, vers celle qui l'a
    absorbée.

COÛT : une collecte entière passe en une dizaine de requêtes, quel que soit le nombre
d'offres (lectures groupées par `IN`, écritures multi-lignes ou `executemany`). Sur une
base distante, c'est le NOMBRE d'allers-retours qui fait la durée (voir project_ctx.md).

VISIBILITÉ : une offre privée (`owner_user_id`, saisie à la main) n'est candidate au
dédoublonnage que pour son auteur. Une collecte ne rattache jamais une annonce publique à
l'offre privée de quelqu'un.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, bindparam, delete, func, or_, select, update
from sqlalchemy.engine import Connection

from jobtool.aggregator.dedup import (
    FUZZY_THRESHOLD,
    PROBABLE_FLOOR,
    Profile,
    best_fields,
    best_match,
    company_key,
    fuzzy_score,
    new_offer_id,
    occurrence_url_keys,
    offer_url_key,
    threshold_for,
    title_key,
    url_match_ok,
)
from jobtool.aggregator.models import Job, JobStatus
from jobtool.aggregator.normalize import NORM_VERSION, normalized_fields, url_native_id
from jobtool.core.db import (
    chunked,
    documents,
    insert_ignore,
    llm_runs,
    occurrence_urls,
    offer_aliases,
    offer_occurrences,
    offers,
    user_offers,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Outcome:
    """Sort d'une offre collectée : l'offre canonique qui la porte, et pourquoi.

    `level` : `identifiant`, `url`, `approchee` ou `nouvelle` (voir aggregator/dedup.py).
    `created` : une offre canonique a été CRÉÉE pour elle.
    """

    offer_id: str
    level: str
    score: float | None
    created: bool


# --- Lignes ---------------------------------------------------------------------

#: Colonnes d'une occurrence lues pour recalculer son offre canonique.
_OCC_FIELDS = (
    "source",
    "native_id",
    "offer_id",
    "url",
    "title",
    "company",
    "location",
    "remote",
    "contract_type",
    "salary",
    "description",
    "posted_date",
    "raw",
    "country",
    "region",
    "department",
    "city",
    "place_precision",
    "contract_kind",
    "work_time",
    "first_seen_at",
    "last_seen_at",
)


def _occurrence_row(job: Job, seen_at: str) -> dict[str, Any]:
    """Occurrence d'une offre collectée (contenu de SA source + clés de comparaison)."""
    return {
        "source": job.source,
        "native_id": job.native_id,
        "url": job.url or "",
        "url_key": offer_url_key(job.url),
        "title": job.title or "",
        "company": job.company or "",
        "location": job.location or "",
        "remote": job.remote or "inconnu",
        "contract_type": job.contract_type or "",
        "salary": job.salary.model_dump() if job.salary else None,
        "description": job.description or "",
        "posted_date": job.posted_date,
        "raw": job.raw or {},
        "country": job.country,
        "region": job.region,
        "department": job.department,
        "city": job.city,
        "place_precision": job.place_precision,
        "contract_kind": job.contract_kind,
        "work_time": job.work_time,
        "company_key": company_key(job.company),
        "title_key": title_key(job.title, job.city),
        "norm_version": NORM_VERSION,
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
    }


def _profile(row: dict[str, Any], sources: Iterable[str]) -> Profile:
    return Profile(
        company_key=row.get("company_key") or "",
        title_key=row.get("title_key") or "",
        department=row.get("department"),
        city=row.get("city"),
        posted_date=row.get("posted_date"),
        contract_kind=row.get("contract_kind") or "inconnu",
        sources=frozenset(sources),
    )


def _visible(owner: uuid.UUID | None) -> Any:
    """Offres candidates au dédoublonnage : le catalogue public, plus les offres privées
    de `owner` s'il s'agit d'une saisie manuelle."""
    if owner is None:
        return offers.c.owner_user_id.is_(None)
    return or_(offers.c.owner_user_id.is_(None), offers.c.owner_user_id == owner)


# --- Lectures groupées ----------------------------------------------------------


def _known_occurrences(
    conn: Connection, keys: set[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Occurrences déjà en base parmi `keys` : (source, native_id) -> {offer_id, first_seen_at}."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    by_source: dict[str, list[str]] = defaultdict(list)
    for source, native_id in keys:
        by_source[source].append(native_id)
    for source, ids in by_source.items():
        for part in chunked(sorted(ids)):
            stmt = select(
                offer_occurrences.c.native_id,
                offer_occurrences.c.offer_id,
                offer_occurrences.c.first_seen_at,
            ).where(
                offer_occurrences.c.source == source,
                offer_occurrences.c.native_id.in_(part),
            )
            for row in conn.execute(stmt):
                found[(source, row.native_id)] = {
                    "offer_id": row.offer_id,
                    "first_seen_at": row.first_seen_at,
                }
    return found


def _offers_by_url(
    conn: Connection, keys: set[str], owner: uuid.UUID | None
) -> dict[str, tuple[str, str, tuple[str, str]]]:
    """Identité d'URL -> (offre visible qui la porte, intitulé de l'occurrence qui porte
    ce lien, clé de cette occurrence). L'intitulé est celui de l'OCCURRENCE : c'est lui
    qu'on compare quand le lien est générique."""
    found: dict[str, tuple[str, str, tuple[str, str]]] = {}
    for part in chunked(sorted(keys)):
        stmt = (
            select(
                occurrence_urls.c.url_key,
                offer_occurrences.c.offer_id,
                offer_occurrences.c.title_key,
                offer_occurrences.c.source,
                offer_occurrences.c.native_id,
            )
            .select_from(
                occurrence_urls.join(
                    offer_occurrences,
                    and_(
                        offer_occurrences.c.source == occurrence_urls.c.source,
                        offer_occurrences.c.native_id == occurrence_urls.c.native_id,
                    ),
                ).join(offers, offers.c.dedup_hash == offer_occurrences.c.offer_id)
            )
            .where(occurrence_urls.c.url_key.in_(part), _visible(owner))
            .order_by(
                offer_occurrences.c.offer_id,
                offer_occurrences.c.source,
                offer_occurrences.c.native_id,
            )
        )
        for row in conn.execute(stmt):
            found.setdefault(
                row.url_key,
                (row.offer_id, row.title_key, (row.source, row.native_id)),
            )
    return found


def _candidates_by_company(
    conn: Connection, keys: set[str], owner: uuid.UUID | None
) -> dict[str, list[tuple[str, Profile]]]:
    """Offres canoniques (visibles) par entreprise normalisée, avec leurs sources."""
    rows: dict[str, dict[str, Any]] = {}
    for part in chunked(sorted(k for k in keys if k)):
        stmt = select(
            offers.c.dedup_hash,
            offers.c.company_key,
            offers.c.title_key,
            offers.c.department,
            offers.c.city,
            offers.c.posted_date,
            offers.c.contract_kind,
        ).where(offers.c.company_key.in_(part), _visible(owner))
        for row in conn.execute(stmt):
            rows[row.dedup_hash] = dict(row._mapping)
    sources = _sources_of(conn, set(rows))
    out: dict[str, list[tuple[str, Profile]]] = defaultdict(list)
    for offer_id, data in sorted(rows.items()):
        out[data["company_key"]].append(
            (offer_id, _profile(data, sources.get(offer_id, ())))
        )
    return out


def _sources_of(conn: Connection, offer_ids: set[str]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for part in chunked(sorted(offer_ids)):
        stmt = select(offer_occurrences.c.offer_id, offer_occurrences.c.source).where(
            offer_occurrences.c.offer_id.in_(part)
        )
        for row in conn.execute(stmt):
            found[row.offer_id].add(row.source)
    return found


def _existing_offer_ids(conn: Connection, ids: set[str]) -> set[str]:
    found: set[str] = set()
    for part in chunked(sorted(ids)):
        stmt = select(offers.c.dedup_hash).where(offers.c.dedup_hash.in_(part))
        found.update(row[0] for row in conn.execute(stmt))
    return found


def _occurrences_of(conn: Connection, offer_ids: set[str]) -> dict[str, list[dict]]:
    """Occurrences (colonnes utiles au recalcul) de chaque offre."""
    found: dict[str, list[dict]] = defaultdict(list)
    columns = [offer_occurrences.c[name] for name in _OCC_FIELDS]
    for part in chunked(sorted(offer_ids)):
        stmt = select(*columns).where(offer_occurrences.c.offer_id.in_(part))
        for row in conn.execute(stmt):
            found[row.offer_id].append(dict(row._mapping))
    return found


# --- Offre canonique ---------------------------------------------------------------


def _canonical_row(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """Colonnes de `offers` recalculées à partir des occurrences d'une offre."""
    best = best_fields(occurrences)
    return {
        **best,
        "url_key": url_native_id(best["url"]),
        "company_key": company_key(best["company"]),
        "title_key": title_key(best["title"], best["city"]),
        "last_seen_at": max(o["last_seen_at"] for o in occurrences),
        "norm_version": NORM_VERSION,
    }


def refresh_offers(conn: Connection, offer_ids: set[str]) -> int:
    """Recalcule les offres canoniques depuis leurs occurrences (écriture groupée).

    Une offre SANS occurrence (reliquat d'avant le modèle d'occurrences) est laissée
    telle quelle : on ne recalcule pas ce qu'on ne sait plus sourcer.
    """
    grouped = _occurrences_of(conn, offer_ids)
    rows = [
        {"_id": offer_id, **_canonical_row(occs)}
        for offer_id, occs in sorted(grouped.items())
        if occs
    ]
    if rows:
        keys = [k for k in rows[0] if k != "_id"]
        conn.execute(
            update(offers)
            .where(offers.c.dedup_hash == bindparam("_id"))
            .values({k: bindparam(k) for k in keys}),
            rows,
        )
    return len(rows)


# --- Écriture d'une collecte --------------------------------------------------------


def upsert_catalogue(
    conn: Connection,
    jobs: Sequence[Job],
    *,
    owner: uuid.UUID | None = None,
) -> list[Outcome]:
    """Range des offres collectées dans le catalogue. Un `Outcome` par offre, DANS L'ORDRE.

    Pour chaque offre, du plus sûr au moins sûr :
      1. même (source, identifiant) déjà connu -> l'occurrence est MISE À JOUR ;
      2. une de ses URL identifie déjà une offre -> rattachée à cette offre ;
      3. une offre de même entreprise, même lieu, intitulé proche -> rattachée ;
      4. sinon, une offre canonique est créée.
    Les décisions valent aussi ENTRE offres d'une même collecte : les ensembles lus en
    base sont tenus à jour au fil de l'eau.

    `owner` : offre PRIVÉE (saisie à la main) de ce compte. Absent : catalogue public.
    Ne commit PAS : l'appelant décide de la transaction.
    """
    if not jobs:
        return []
    now = _now()
    occ_rows = [_occurrence_row(job, job.fetched_date or now) for job in jobs]
    url_sets = [occurrence_url_keys(job.url, job.raw) for job in jobs]

    known = _known_occurrences(conn, {(j.source, j.native_id) for j in jobs})
    by_url = _offers_by_url(conn, set().union(*url_sets), owner)
    by_company = _candidates_by_company(
        conn, {r["company_key"] for r in occ_rows}, owner
    )
    taken_ids = _existing_offer_ids(conn, {j.dedup_hash for j in jobs})

    outcomes: list[Outcome] = []
    new_offer_owner: dict[str, uuid.UUID | None] = {}
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    seen_in_run: dict[tuple[str, str], str] = {}

    for job, row, urls in zip(jobs, occ_rows, url_sets, strict=True):
        key = (job.source, job.native_id)
        level, score, created = "identifiant", None, False
        if key in seen_in_run:
            offer_id = seen_in_run[key]
        elif key in known:
            offer_id = known[key]["offer_id"]
            row["first_seen_at"] = known[key]["first_seen_at"]
            updates.append(row)
        else:
            match_url = next(
                (
                    by_url[u][0]
                    for u in sorted(urls)
                    if u in by_url and url_match_ok(u, row["title_key"], by_url[u][1])
                ),
                None,
            )
            fuzzy = None
            if match_url is None and row["company_key"]:
                fuzzy = best_match(
                    _profile(row, {job.source}), by_company.get(row["company_key"], [])
                )
            if match_url is not None:
                offer_id, level = match_url, "url"
            elif fuzzy is not None:
                (offer_id, score), level = fuzzy, "approchee"
            else:
                level, created = "nouvelle", True
                offer_id = job.dedup_hash
                if not offer_id or offer_id in taken_ids:
                    # Même triplet (entreprise, intitulé, lieu) qu'une offre existante
                    # SANS que les règles aient conclu au doublon (entreprise vide, même
                    # source…) : on ne confond pas, on crée une offre distincte.
                    offer_id = new_offer_id(job.source, job.native_id)
                taken_ids.add(offer_id)
                new_offer_owner[offer_id] = owner
            row.update(match_level=level, match_score=score)
            inserts.append(row)

        row["offer_id"] = offer_id
        seen_in_run[key] = offer_id
        for u in urls:
            # Une occurrence mise à jour dans la run remplace sa version précédente.
            if u not in by_url or by_url[u][2] == key:
                by_url[u] = (offer_id, row["title_key"], key)
        if row["company_key"]:
            # L'offre (ou sa nouvelle source) devient candidate pour la suite de la run.
            group = by_company[row["company_key"]]
            existing = next((p for oid, p in group if oid == offer_id), None)
            sources = {job.source} | (set(existing.sources) if existing else set())
            group[:] = [(oid, p) for oid, p in group if oid != offer_id]
            group.append((offer_id, _profile(row, sources)))
        outcomes.append(Outcome(offer_id, level, score, created))

    # 1. Offres canoniques CRÉÉES : insérées d'abord (les occurrences les référencent).
    new_ids = set(new_offer_owner)
    fresh: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inserts:
        if row["offer_id"] in new_ids:
            fresh[row["offer_id"]].append(row)
    offer_rows = []
    for offer_id, occs in sorted(fresh.items()):
        canonical = _canonical_row(occs)
        offer_rows.append(
            {
                **canonical,
                "dedup_hash": offer_id,
                "id": offer_id,
                "fetched_date": min(o["first_seen_at"] for o in occs),
                "owner_user_id": new_offer_owner[offer_id],
            }
        )
    insert_ignore(conn, offers, offer_rows)

    # 2. Occurrences : nouvelles insérées, connues mises à jour (contenu, date de revue).
    insert_ignore(conn, offer_occurrences, _dedupe(inserts))
    if updates:
        keys = [k for k in updates[0] if k not in ("source", "native_id")]
        conn.execute(
            update(offer_occurrences)
            .where(
                offer_occurrences.c.source == bindparam("_source"),
                offer_occurrences.c.native_id == bindparam("_native_id"),
            )
            .values({k: bindparam(k) for k in keys}),
            [
                {**r, "_source": r["source"], "_native_id": r["native_id"]}
                for r in _dedupe(updates)
            ],
        )
    insert_ignore(
        conn,
        occurrence_urls,
        _dedupe(
            [
                {"url_key": u, "source": j.source, "native_id": j.native_id}
                for j, urls in zip(jobs, url_sets, strict=True)
                for u in urls
            ],
            key=("url_key", "source", "native_id"),
        ),
    )

    # 3. Offres EXISTANTES qui ont reçu ou mis à jour une occurrence : recalculées, et
    #    plus expirées puisqu'une source vient de la revoir.
    touched = {o.offer_id for o in outcomes} - new_ids
    refresh_offers(conn, touched)
    for part in chunked(sorted(touched)):
        conn.execute(
            update(offers)
            .where(offers.c.dedup_hash.in_(part), offers.c.expired_at.is_not(None))
            .values(expired_at=None)
        )
    return outcomes


def _dedupe(
    rows: list[dict[str, Any]], key: tuple[str, ...] = ("source", "native_id")
) -> list[dict[str, Any]]:
    """Dernière version de chaque ligne par clé (une offre vue deux fois dans une run)."""
    last: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        last[tuple(row[k] for k in key)] = row
    return list(last.values())


# --- Fusion d'offres canoniques -------------------------------------------------------


@dataclass(frozen=True)
class Merge:
    """Décision de fusion : `drop` est absorbée par `keep`, pour la raison donnée."""

    drop: str
    keep: str
    level: str
    score: float | None = None


#: Ordre d'engagement des statuts : à la fusion, un compte garde le plus engagé.
_STATUS_RANK = {
    JobStatus.applied.value: 3,
    JobStatus.rejected.value: 2,
    JobStatus.seen.value: 1,
    JobStatus.new.value: 0,
}


def _combine_states(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """État d'un compte sur deux offres fusionnées — RIEN n'est perdu.

    Favori si l'une l'était ; statut le plus engagé (postulée > rejetée > masquée >
    nouvelle) avec sa date de masquage ; date de candidature la plus ANCIENNE ;
    description corrigée de l'offre conservée, sinon de l'autre ; entrée la plus ancienne.
    """
    lead = max((a, b), key=lambda s: _STATUS_RANK.get(s["status"], 0))
    applied = sorted(x for x in (a["applied_at"], b["applied_at"]) if x)
    return {
        "status": lead["status"],
        "hidden_at": lead["hidden_at"],
        "favorite": bool(a["favorite"] or b["favorite"]),
        "applied_at": applied[0] if applied else None,
        "description_override": a["description_override"] or b["description_override"],
        "added_at": min(a["added_at"], b["added_at"]),
    }


def _resolve(merges: Sequence[Merge]) -> dict[str, Merge]:
    """drop -> décision FINALE (chaînes A->B->C ramenées à A->C)."""
    target = {m.drop: m for m in merges}

    def final(offer_id: str, depth: int = 0) -> str:
        nxt = target.get(offer_id)
        if nxt is None or depth > len(target):
            return offer_id
        return final(nxt.keep, depth + 1)

    return {
        drop: Merge(drop, final(m.keep), m.level, m.score)
        for drop, m in target.items()
        if final(m.keep) != drop
    }


def merge_offers(conn: Connection, merges: Sequence[Merge]) -> int:
    """Fusionne des offres canoniques, en GROUPE. Renvoie le nombre d'offres absorbées.

    Pour chaque fusion : les occurrences passent à l'offre conservée, l'état de chaque
    compte y est REPORTÉ (combiné s'il existait des deux côtés, voir `_combine_states`),
    les documents et le journal de génération suivent, et l'ancien identifiant devient un
    alias. L'offre absorbée est ensuite supprimée. Ne commit PAS.
    """
    plan = _resolve(merges)
    if not plan:
        return 0
    drops = sorted(plan)
    moved = [{"_drop": d, "_keep": plan[d].keep} for d in drops]
    now = _now()

    # Occurrences.
    conn.execute(
        update(offer_occurrences)
        .where(offer_occurrences.c.offer_id == bindparam("_drop"))
        .values(offer_id=bindparam("_keep")),
        moved,
    )

    # État par compte : lu en une fois pour les deux côtés, combiné en mémoire.
    involved = set(drops) | {plan[d].keep for d in drops}
    states: dict[tuple[Any, str], dict[str, Any]] = {}
    for part in chunked(sorted(involved)):
        for row in conn.execute(
            select(user_offers).where(user_offers.c.dedup_hash.in_(part))
        ):
            states[(row.user_id, row.dedup_hash)] = dict(row._mapping)
    combined: dict[tuple[Any, str], dict[str, Any]] = {}
    for (user_id, offer_id), state in states.items():
        if offer_id not in plan:
            continue
        keep = plan[offer_id].keep
        base = combined.get((user_id, keep)) or states.get((user_id, keep))
        combined[(user_id, keep)] = (
            _combine_states(base, state) if base else {**state, "dedup_hash": keep}
        )
    for part in chunked(drops):
        conn.execute(delete(user_offers).where(user_offers.c.dedup_hash.in_(part)))
    state_cols = (
        "status",
        "hidden_at",
        "favorite",
        "applied_at",
        "description_override",
        "added_at",
    )
    kept_updates = [
        {"_uid": user_id, "_keep": keep, **{c: state[c] for c in state_cols}}
        for (user_id, keep), state in combined.items()
        if (user_id, keep) in states
    ]
    if kept_updates:
        conn.execute(
            update(user_offers)
            .where(
                user_offers.c.user_id == bindparam("_uid"),
                user_offers.c.dedup_hash == bindparam("_keep"),
            )
            .values({c: bindparam(c) for c in state_cols}),
            kept_updates,
        )
    insert_ignore(
        conn,
        user_offers,
        [
            {**state, "user_id": user_id, "dedup_hash": keep}
            for (user_id, keep), state in combined.items()
            if (user_id, keep) not in states
        ],
    )

    # Documents, journal des générations : ils citent l'offre par son identifiant.
    for table in (documents, llm_runs):
        conn.execute(
            update(table)
            .where(table.c.offer_id == bindparam("_drop"))
            .values(offer_id=bindparam("_keep")),
            moved,
        )

    # Alias : l'ancien identifiant, et ceux qui pointaient déjà vers lui.
    conn.execute(
        update(offer_aliases)
        .where(offer_aliases.c.offer_id == bindparam("_drop"))
        .values(offer_id=bindparam("_keep")),
        moved,
    )
    insert_ignore(
        conn,
        offer_aliases,
        [
            {
                "old_id": d,
                "offer_id": plan[d].keep,
                "level": plan[d].level,
                "score": plan[d].score,
                "merged_at": now,
            }
            for d in drops
        ],
    )
    for part in chunked(drops):
        conn.execute(delete(offers).where(offers.c.dedup_hash.in_(part)))
    refresh_offers(conn, {plan[d].keep for d in drops})
    return len(drops)


def resolve_offer_id(conn: Connection, offer_id: str) -> str:
    """Identifiant ACTUEL d'une offre : lui-même, ou l'offre qui l'a absorbée."""
    row = conn.execute(
        select(offer_aliases.c.offer_id).where(offer_aliases.c.old_id == offer_id)
    ).first()
    return row.offer_id if row is not None else offer_id


# --- Dédoublonnage de tout le catalogue ------------------------------------------------


@dataclass(frozen=True)
class Pair:
    """Deux offres canoniques comparées : `score` de similarité, niveau de décision."""

    a: str
    b: str
    level: str
    score: float | None


def _catalogue_profiles(conn: Connection) -> dict[str, dict[str, Any]]:
    """Offres PUBLIQUES du catalogue, avec ce que les niveaux 2 et 3 comparent."""
    rows = {
        row.dedup_hash: dict(row._mapping)
        for row in conn.execute(
            select(
                offers.c.dedup_hash,
                offers.c.title,
                offers.c.company,
                offers.c.location,
                offers.c.company_key,
                offers.c.title_key,
                offers.c.department,
                offers.c.city,
                offers.c.posted_date,
                offers.c.contract_kind,
                offers.c.fetched_date,
            ).where(offers.c.owner_user_id.is_(None))
        )
    }
    for offer_id, sources in _sources_of(conn, set(rows)).items():
        if offer_id in rows:
            rows[offer_id]["sources"] = sources
    urls: dict[str, set[str]] = defaultdict(set)
    stmt = select(occurrence_urls.c.url_key, offer_occurrences.c.offer_id).select_from(
        occurrence_urls.join(
            offer_occurrences,
            and_(
                offer_occurrences.c.source == occurrence_urls.c.source,
                offer_occurrences.c.native_id == occurrence_urls.c.native_id,
            ),
        )
    )
    for link in conn.execute(stmt):
        urls[link.offer_id].add(link.url_key)
    for offer_id, data in rows.items():
        data.setdefault("sources", set())
        data["urls"] = urls.get(offer_id, set())
    return rows


def compare_catalogue(conn: Connection) -> list[Pair]:
    """Toutes les paires d'offres PUBLIQUES comparables : liens communs (niveau 2), et
    entreprise + lieu compatibles avec un score d'intitulé (niveau 3, tous scores).

    Sert à la fois au dédoublonnage du catalogue (paires au-dessus du seuil) et à la
    liste des doublons probables (juste en dessous). Les comparaisons sont limitées aux
    offres d'une même entreprise : le coût suit la taille des entreprises, pas le carré
    du catalogue.
    """
    rows = _catalogue_profiles(conn)
    pairs: list[Pair] = []
    owners: dict[str, str] = {}
    for offer_id in sorted(rows):
        for url in sorted(rows[offer_id]["urls"]):
            other = owners.setdefault(url, offer_id)
            if other != offer_id and url_match_ok(
                url, rows[other]["title_key"], rows[offer_id]["title_key"]
            ):
                pairs.append(Pair(other, offer_id, "url", None))
    by_company: dict[str, list[str]] = defaultdict(list)
    for offer_id, row in sorted(rows.items()):
        if row["company_key"]:
            by_company[row["company_key"]].append(offer_id)
    for ids in by_company.values():
        for i, a in enumerate(ids):
            pa = _profile(rows[a], rows[a]["sources"])
            for b in ids[i + 1 :]:
                pb = _profile(rows[b], rows[b]["sources"])
                score = fuzzy_score(pa, pb)
                if score is not None:
                    level = (
                        "approchee" if score >= threshold_for(pa, pb) else "sous_seuil"
                    )
                    pairs.append(Pair(a, b, level, score))
    return pairs


def _keep_of(rows: dict[str, dict[str, Any]], a: str, b: str) -> tuple[str, str]:
    """(conservée, absorbée) : la plus ANCIENNE au catalogue garde son identifiant, pour
    que les liens et favoris existants restent stables."""
    ka = (rows[a].get("fetched_date") or "", a)
    kb = (rows[b].get("fetched_date") or "", b)
    return (a, b) if ka <= kb else (b, a)


def deduplicate_catalogue(conn: Connection, *, apply: bool = True) -> list[Merge]:
    """Fusionne les doublons DÉJÀ présents dans le catalogue (niveaux 2 et 3).

    Utile après une évolution des règles, et pour les offres collectées avant le
    dédoublonnage entre sources. Les fusions approchées gardent la PRUDENCE du niveau 3 :
    une offre n'est absorbée qu'une fois, par la paire au meilleur score.
    """
    orphan_merges = _orphans(conn)
    rows = _catalogue_profiles(conn)
    decided: list[Merge] = list(orphan_merges)
    absorbed: set[str] = {m.drop for m in orphan_merges}
    pairs = [p for p in compare_catalogue(conn) if p.level in ("url", "approchee")]
    pairs.sort(key=lambda p: (p.level != "url", -(p.score or 1.0), p.a, p.b))
    for pair in pairs:
        if pair.a in absorbed or pair.b in absorbed:
            continue
        keep, drop = _keep_of(rows, pair.a, pair.b)
        # Deux offres d'une même source ne fusionnent jamais, même par l'union des
        # sources d'une fusion précédente.
        if pair.level == "approchee" and rows[keep]["sources"] & rows[drop]["sources"]:
            continue
        decided.append(Merge(drop, keep, pair.level, pair.score))
        absorbed.add(drop)
        rows[keep]["sources"] = rows[keep]["sources"] | rows[drop]["sources"]
    if apply and decided:
        merge_offers(conn, decided)
    return decided


def _orphans(conn: Connection) -> list[Merge]:
    """Offres SANS occurrence dont l'identité (source, identifiant) appartient à une autre
    offre : reliquats d'avant le modèle d'occurrences, fusionnés au niveau `identifiant`."""
    own = offer_occurrences.alias("own")
    stmt = (
        select(offers.c.dedup_hash, offer_occurrences.c.offer_id)
        .select_from(
            offers.join(
                offer_occurrences,
                and_(
                    offer_occurrences.c.source == offers.c.source,
                    offer_occurrences.c.native_id == offers.c.native_id,
                    offer_occurrences.c.offer_id != offers.c.dedup_hash,
                ),
            )
        )
        .where(
            ~select(own.c.offer_id)
            .where(own.c.offer_id == offers.c.dedup_hash)
            .exists()
        )
    )
    return [
        Merge(row.dedup_hash, row.offer_id, "identifiant") for row in conn.execute(stmt)
    ]


def probable_duplicates(
    conn: Connection, *, low: float = PROBABLE_FLOOR, high: float = FUZZY_THRESHOLD
) -> list[tuple[Pair, dict[str, Any], dict[str, Any]]]:
    """Paires comparables dont le score est dans [low ; high[ : les « presque doublons »
    que le seuil laisse passer, pour le calibrer à l'œil. Les plus proches d'abord."""
    rows = _catalogue_profiles(conn)
    found = [
        (p, rows[p.a], rows[p.b])
        for p in compare_catalogue(conn)
        if p.score is not None and low <= p.score < high
    ]
    found.sort(key=lambda t: (-(t[0].score or 0), t[0].a, t[0].b))
    return found


# --- Normalisation des occurrences déjà en base -----------------------------------------


def renormalize_catalogue(conn: Connection, *, batch: int = 500) -> int:
    """Recalcule les champs normalisés des occurrences produites par une règle ANTÉRIEURE.

    Les occurrences d'avant une évolution des règles (`NORM_VERSION`) — ou recopiées
    depuis l'ancien modèle — n'ont pas de département, de contrat ni de clés de
    comparaison exploitables : sans ce rattrapage, elles échapperaient aux filtres et au
    dédoublonnage. Leurs offres canoniques sont recalculées ensuite. Idempotent, sans
    effet (une requête) quand tout est à jour, écrit par lots.
    """
    total = 0
    touched: set[str] = set()
    while True:
        rows = conn.execute(
            select(
                offer_occurrences.c.source,
                offer_occurrences.c.native_id,
                offer_occurrences.c.offer_id,
                offer_occurrences.c.title,
                offer_occurrences.c.company,
                offer_occurrences.c.location,
                offer_occurrences.c.contract_type,
                offer_occurrences.c.url,
                offer_occurrences.c.raw,
            )
            .where(offer_occurrences.c.norm_version < NORM_VERSION)
            .limit(batch)
        ).all()
        if not rows:
            break
        updates = []
        url_rows = []
        for row in rows:
            fields = normalized_fields(
                row.source, row.title, row.location, row.contract_type, row.raw
            )
            updates.append(
                {
                    "_source": row.source,
                    "_native_id": row.native_id,
                    **fields,
                    "company_key": company_key(row.company),
                    "title_key": title_key(row.title, fields["city"]),
                    "url_key": offer_url_key(row.url),
                    "norm_version": NORM_VERSION,
                }
            )
            url_rows += [
                {"url_key": u, "source": row.source, "native_id": row.native_id}
                for u in occurrence_url_keys(row.url, row.raw)
            ]
            touched.add(row.offer_id)
        keys = [k for k in updates[0] if not k.startswith("_")]
        conn.execute(
            update(offer_occurrences)
            .where(
                offer_occurrences.c.source == bindparam("_source"),
                offer_occurrences.c.native_id == bindparam("_native_id"),
            )
            .values({k: bindparam(k) for k in keys}),
            updates,
        )
        # Les identités d'URL sont RECALCULÉES : les anciennes (règle antérieure) sont
        # retirées d'abord, sinon l'index garderait des rapprochements périmés.
        by_source: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            by_source[row.source].append(row.native_id)
        for source, ids in by_source.items():
            conn.execute(
                delete(occurrence_urls).where(
                    occurrence_urls.c.source == source,
                    occurrence_urls.c.native_id.in_(ids),
                )
            )
        insert_ignore(conn, occurrence_urls, url_rows)
        total += len(rows)
    if touched:
        refresh_offers(conn, touched)
    conn.commit()
    return total


# --- Lectures de provenance -------------------------------------------------------------


def occurrences_for(
    conn: Connection, offer_ids: Sequence[str]
) -> dict[str, list[dict]]:
    """Provenance de chaque offre : source, identifiant, lien, niveau de rattachement.

    Groupée (une requête par tranche) : une page d'offres en affiche toutes les sources.
    Ordre : priorité des sources, puis identifiant — le lien principal d'abord.
    """
    from jobtool.aggregator.dedup import source_priority

    found: dict[str, list[dict]] = defaultdict(list)
    for part in chunked(sorted(set(offer_ids))):
        stmt = select(
            offer_occurrences.c.offer_id,
            offer_occurrences.c.source,
            offer_occurrences.c.native_id,
            offer_occurrences.c.url,
            offer_occurrences.c.first_seen_at,
            offer_occurrences.c.last_seen_at,
            offer_occurrences.c.match_level,
            offer_occurrences.c.match_score,
        ).where(offer_occurrences.c.offer_id.in_(part))
        for row in conn.execute(stmt):
            found[row.offer_id].append(dict(row._mapping))
    for occs in found.values():
        occs.sort(key=lambda o: (source_priority(o["source"]), o["native_id"]))
    return found


def _grouped_count(conn: Connection, column: Any) -> dict[str, int]:
    stmt = select(column, func.count()).group_by(column)
    return {str(row[0]): int(row[1]) for row in conn.execute(stmt)}


def catalogue_stats(conn: Connection) -> dict[str, Any]:
    """Volumes du catalogue : occurrences et offres par source, rattachements par niveau."""
    by_source = _grouped_count(conn, offer_occurrences.c.source)
    by_level = _grouped_count(conn, offer_occurrences.c.match_level)
    total_offers = int(
        conn.execute(select(func.count()).select_from(offers)).scalar() or 0
    )
    multi = int(
        conn.execute(
            select(func.count()).select_from(
                select(offer_occurrences.c.offer_id)
                .group_by(offer_occurrences.c.offer_id)
                .having(func.count() > 1)
                .subquery()
            )
        ).scalar()
        or 0
    )
    aliases = _grouped_count(conn, offer_aliases.c.level)
    return {
        "occurrences": sum(by_source.values()),
        "occurrences_by_source": by_source,
        "occurrences_by_level": by_level,
        "offers": total_offers,
        "offers_with_several_sources": multi,
        "merges_by_level": aliases,
    }


# --- Expiration du catalogue ------------------------------------------------------------


@dataclass(frozen=True)
class PurgeResult:
    """Bilan d'une purge : offres supprimées, et offres expirées mais CONSERVÉES."""

    deleted: int
    kept_expired: int


def purge_expired(
    conn: Connection, *, cutoff: str, healthy_sources: set[str]
) -> PurgeResult:
    """Retire du catalogue les offres EXPIRÉES — jamais celles auxquelles un compte tient.

    Expirée : aucune collecte ne l'a revue depuis `cutoff`, alors que TOUTES ses sources
    ont été collectées avec succès depuis (`healthy_sources`). Ne plus voir une offre
    d'une source en panne, ou non interrogée, ne prouve pas qu'elle a disparu.

    Protégée (conservée, marquée `expired_at`) : favorite, postulée ou refusée, décrite à
    la main, ou citée par un document généré. Les offres PRIVÉES (saisies à la main) ne
    sont jamais concernées. Une offre masquée n'est pas protégée : masquer, c'est déjà
    dire qu'on n'en veut plus. Ne commit PAS.
    """
    last_seen = func.coalesce(offers.c.last_seen_at, offers.c.fetched_date)
    candidates = {
        row[0]
        for row in conn.execute(
            select(offers.c.dedup_hash).where(
                offers.c.owner_user_id.is_(None), last_seen < cutoff
            )
        )
    }
    if not candidates:
        return PurgeResult(0, 0)
    sources = _sources_of(conn, candidates)
    expired = {
        oid
        for oid in candidates
        if sources.get(oid) and sources[oid] <= healthy_sources
    }
    if not expired:
        return PurgeResult(0, 0)

    protected: set[str] = set()
    for part in chunked(sorted(expired)):
        protected.update(
            row[0]
            for row in conn.execute(
                select(user_offers.c.dedup_hash).where(
                    user_offers.c.dedup_hash.in_(part),
                    or_(
                        user_offers.c.favorite.is_(True),
                        user_offers.c.applied_at.is_not(None),
                        user_offers.c.status.in_(
                            (JobStatus.applied.value, JobStatus.rejected.value)
                        ),
                        user_offers.c.description_override.is_not(None),
                    ),
                )
            )
        )
        protected.update(
            row[0]
            for row in conn.execute(
                select(documents.c.offer_id).where(documents.c.offer_id.in_(part))
            )
        )
    now = _now()
    for part in chunked(sorted(protected)):
        conn.execute(
            update(offers)
            .where(offers.c.dedup_hash.in_(part), offers.c.expired_at.is_(None))
            .values(expired_at=now)
        )

    doomed = sorted(expired - protected)
    for part in chunked(doomed):
        occs = conn.execute(
            select(offer_occurrences.c.source, offer_occurrences.c.native_id).where(
                offer_occurrences.c.offer_id.in_(part)
            )
        ).all()
        by_source: dict[str, list[str]] = defaultdict(list)
        for occ in occs:
            by_source[occ.source].append(occ.native_id)
        for source, ids in by_source.items():
            for ids_part in chunked(ids):
                conn.execute(
                    delete(occurrence_urls).where(
                        occurrence_urls.c.source == source,
                        occurrence_urls.c.native_id.in_(ids_part),
                    )
                )
        conn.execute(
            delete(offer_occurrences).where(offer_occurrences.c.offer_id.in_(part))
        )
        conn.execute(delete(user_offers).where(user_offers.c.dedup_hash.in_(part)))
        conn.execute(delete(offer_aliases).where(offer_aliases.c.offer_id.in_(part)))
        conn.execute(delete(offers).where(offers.c.dedup_hash.in_(part)))
    return PurgeResult(deleted=len(doomed), kept_expired=len(protected))
