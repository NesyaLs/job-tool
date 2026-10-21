"""Routes /offers : liste, détail, statut, favori, description, génération CV/lettre.

CHAQUE route suit le même contrat : recevoir -> appeler un service -> renvoyer du JSON (ou un
PDF). Aucune logique métier, aucun SQL, aucun accès domaine en direct : tout passe par
`OffersService` (injecté) et le `Generator` (injecté). Erreurs : offre introuvable -> 404 ;
génération en échec -> 502 + corps JSON explicite (jamais un faux 200). Les secrets ne
transitent jamais dans les réponses (les services ne renvoient que des messages assainis).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse

from jobtool.aggregator.models import Job, JobStatus
from jobtool.aggregator.contracts import ContractKind as ContractFilter
from jobtool.aggregator.matching import CONTRACT_KINDS, ContractKind, MatchCriteria
from jobtool.api.deps import get_discovery_service, get_generator, get_offers_service
from jobtool.api.generation import GenerationOutcome, Generator
from jobtool.api.schemas import (
    DescriptionUpdate,
    FavoriteUpdate,
    GenerationError,
    ManualOfferCreate,
    OfferDetail,
    OfferSummary,
    StackCard,
    StatusUpdate,
)
from jobtool.services.discovery_service import DiscoveryService
from jobtool.services.offers_service import (
    OfferOrder,
    OffersFilter,
    OffersService,
    RankedOrder,
)

router = APIRouter(prefix="/offers", tags=["offers"])


def _get_offer_or_404(service: OffersService, offer_id: str) -> Job:
    """Charge une offre par id (dedup_hash) ou lève 404 — factorisé pour toutes les routes."""
    offer = service.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail=f"offre introuvable : {offer_id}")
    return offer


# --- Lectures ---------------------------------------------------------------


@router.get("", response_model=list[OfferSummary])
def list_offers(
    response: Response,
    q: str = "",
    keyword: str = "",
    location: str = "",
    departments: str = "",
    contract: list[ContractFilter] = Query(default=[]),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    include_unknown_location: bool = False,
    source: str | None = None,
    status: JobStatus | None = None,
    favorite: bool | None = None,
    order: OfferOrder = "fetched_date",
    max_age_days: int | None = Query(default=None, ge=1),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: OffersService = Depends(get_offers_service),
) -> list[OfferSummary]:
    """RECHERCHE dans le catalogue : filtres appliqués par la base, réponse immédiate.

    Aucune collecte n'est lancée ici, et rien n'est masqué : une recherche ne change
    l'état d'aucune offre (voir POST /aggregate pour actualiser le catalogue).

    `q` : chaque mot doit figurer dans l'intitulé ou l'entreprise (sans casse ni accents).
    `departments` : codes INSEE séparés par des virgules (lieu NORMALISÉ ; le télétravail
    complet passe). `contract` : familles normalisées, répétable. `country` : ISO2 (« FR »).
    `include_unknown_location` : garder, sous un filtre de lieu, les offres dont le lieu
    est inexploitable (écartées par défaut). `keyword` / `location` : sous-chaînes du
    texte d'origine (affinage), comme avant.

    `order` : `fetched_date` (défaut, ordre historique), `posted_date` (plus récemment publiées)
    ou `applied_at` (dernières candidatures). Typé en Literal -> une valeur hors liste est
    rejetée en 422, elle n'atteint jamais le SQL ; le tri est fait en base (storage).

    `max_age_days` : ne garde que les offres PUBLIÉES dans les N derniers jours. Absent = aucun
    filtre d'âge. Filtre et tri s'empilent : le filtre restreint l'ensemble, le tri l'ordonne.

    `limit` / `offset` : pagination. Le corps reste une LISTE — le total part dans l'en-tête
    `X-Total-Count`, ce qui laisse le contrat de réponse inchangé pour tous les appelants qui
    ne paginent pas. Sans `limit`, tout est renvoyé, comme avant.

    Le total est celui des offres CORRESPONDANT AU FILTRE, pas celui du compte : c'est lui qui
    permet à l'appelant de savoir s'il existe une page suivante.
    """
    flt = OffersFilter(
        status=status,
        search=q,
        keyword=keyword,
        location=location,
        departments=tuple(d.strip() for d in departments.split(",") if d.strip()),
        contracts=tuple(dict.fromkeys(contract)),
        country=country.upper() if country else None,
        include_unknown_location=include_unknown_location,
        source=source or None,
        favorite=favorite,
        order=order,
        max_age_days=max_age_days,
    )
    if limit is None:
        jobs = service.list_offers(flt)
        response.headers["X-Total-Count"] = str(len(jobs))
        return [OfferSummary.from_job(job) for job in jobs]

    page = service.list_page(flt, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(page.total)
    return [OfferSummary.from_job(job) for job in page.items]


@router.get("/stack", response_model=list[StackCard])
def offers_stack(
    response: Response,
    keyword: str = "",
    departments: str = "",
    contract: list[ContractKind] = Query(default=[]),
    max_age_days: int | None = Query(default=None, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    exclude: list[str] = Query(default=[]),
    service: DiscoveryService = Depends(get_discovery_service),
) -> list[StackCard]:
    """Pile de la page « Découvrir » : offres à traiter, de la plus compatible à la moins.

    Seules les offres `new` et non favorites y figurent : « j'aime » (favori) et
    « masquer » (statut `seen`) passent par les routes habituelles et la sortent donc
    d'eux-mêmes. `departments` : codes INSEE séparés par des virgules, comme `location`
    pour /aggregate. `exclude` : ids déjà affichés côté client (préchargement sans doublon).

    Déclarée AVANT `/{offer_id}` : sinon « stack » serait lu comme un identifiant d'offre.
    L'en-tête `X-Total-Count` donne le nombre d'offres restant à proposer (hors `exclude`).
    """
    criteria = MatchCriteria(
        keywords=keyword,
        departments=tuple(d.strip() for d in departments.split(",") if d.strip()),
        contracts=tuple(dict.fromkeys(contract)),
    )
    page = service.stack(
        criteria, limit=limit, exclude=exclude, max_age_days=max_age_days
    )
    response.headers["X-Total-Count"] = str(page.remaining)
    return [StackCard.from_match(match) for match in page.items]


@router.get("/ranked", response_model=list[StackCard])
def ranked_offers(
    response: Response,
    keywords: str = "",
    q: str = "",
    departments: str = "",
    contract: list[ContractFilter] = Query(default=[]),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    include_unknown_location: bool = False,
    location: str = "",
    status: JobStatus | None = None,
    favorite: bool | None = None,
    max_age_days: int | None = Query(default=None, ge=1),
    order: RankedOrder = "score",
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: OffersService = Depends(get_offers_service),
) -> list[StackCard]:
    """Offres FILTRÉES comme `GET /offers`, et NOTÉES comme la pile « Découvrir ».

    Sert les dossiers de l'interface (Nouvelles, Favoris, Candidatures, Masquées), dont
    chaque ligne affiche un score. Filtres durs : ceux de `GET /offers` (`q`, lieu,
    contrat, statut, favori, fraîcheur). Critères de score : `keywords` (mots-clés
    recherchés), plus le lieu et le contrat demandés. Contrairement à la pile, aucune
    offre n'est écartée sous un seuil.

    `order` : `score` (défaut, décroissant), `posted_date` ou `applied_at` (tri SQL,
    chaque offre portant son score). Total dans `X-Total-Count`. Déclarée AVANT
    `/{offer_id}` : sinon « ranked » serait lu comme un identifiant d'offre.
    """
    deps_codes = tuple(d.strip() for d in departments.split(",") if d.strip())
    contracts = tuple(dict.fromkeys(contract))
    flt = OffersFilter(
        status=status,
        search=q,
        location=location,
        departments=deps_codes,
        contracts=contracts,
        country=country.upper() if country else None,
        include_unknown_location=include_unknown_location,
        favorite=favorite,
        max_age_days=max_age_days,
    )
    criteria = MatchCriteria(
        keywords=keywords,
        departments=deps_codes,
        contracts=tuple(c for c in contracts if c in CONTRACT_KINDS),  # type: ignore[misc]
    )
    page = service.list_ranked(flt, criteria, order=order, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(page.total)
    return [StackCard.from_match(match) for match in page.items]


@router.post("", response_model=OfferDetail, status_code=201)
def create_offer(
    body: ManualOfferCreate,
    service: OffersService = Depends(get_offers_service),
) -> OfferDetail:
    """Enregistre une offre saisie à la main (que l'agrégateur n'a pas récupérée).

    201 + l'offre créée. 409 si le LIEN est déjà connu (l'annonce est déjà en base, sous
    quelque source que ce soit) : on ne crée pas de doublon et on n'écrase pas l'existant.
    """
    result = service.create_manual_offer(
        titre=body.titre,
        url=body.url,
        description=body.description,
        entreprise=body.entreprise,
        lieu=body.lieu,
    )
    if not result.created:
        raise HTTPException(
            status_code=409,
            detail=f"offre déjà enregistrée pour ce lien (id : {result.job.dedup_hash})",
        )
    return OfferDetail.from_job(result.job)


@router.get("/{offer_id}", response_model=OfferDetail)
def get_offer(
    offer_id: str,
    service: OffersService = Depends(get_offers_service),
) -> OfferDetail:
    """Renvoie une offre complète, ou 404 si l'id est inconnu."""
    return OfferDetail.from_job(_get_offer_or_404(service, offer_id))


# --- Écritures (statut / favori / description) ------------------------------


@router.patch("/{offer_id}/status", response_model=OfferDetail)
def update_status(
    offer_id: str,
    body: StatusUpdate,
    service: OffersService = Depends(get_offers_service),
) -> OfferDetail:
    """Change le statut (new/seen/applied/rejected). 404 si l'offre n'existe pas."""
    if not service.update_status(offer_id, body.status):
        raise HTTPException(status_code=404, detail=f"offre introuvable : {offer_id}")
    return OfferDetail.from_job(_get_offer_or_404(service, offer_id))


@router.patch("/{offer_id}/favorite", response_model=OfferDetail)
def update_favorite(
    offer_id: str,
    body: FavoriteUpdate | None = None,
    service: OffersService = Depends(get_offers_service),
) -> OfferDetail:
    """Bascule (corps absent) ou force (corps `{favorite: bool}`) le drapeau favori. 404 sinon."""
    offer = _get_offer_or_404(service, offer_id)
    target = (
        not offer.favorite if body is None or body.favorite is None else body.favorite
    )
    service.set_favorite(offer_id, target)
    return OfferDetail.from_job(_get_offer_or_404(service, offer_id))


@router.patch("/{offer_id}/description", response_model=OfferDetail)
def update_description(
    offer_id: str,
    body: DescriptionUpdate,
    service: OffersService = Depends(get_offers_service),
) -> OfferDetail:
    """Remplace la description (cas d'une description source partielle, complétée à la main)."""
    if not service.update_description(offer_id, body.description):
        raise HTTPException(status_code=404, detail=f"offre introuvable : {offer_id}")
    return OfferDetail.from_job(_get_offer_or_404(service, offer_id))


# --- Génération CV / lettre (bloquante : on attend le PDF) -------------------


def _pdf_or_error(
    outcome: GenerationOutcome, *, offer_id: str, kind: str, filename: str
) -> FileResponse | JSONResponse:
    """Traduit une issue de génération en réponse HTTP : PDF (succès) ou 502 JSON (échec).

    Jamais un faux 200 : si le PDF est absent (ou disparu), on renvoie 502 + raison lisible.
    """
    if outcome.ok and outcome.pdf_path and Path(outcome.pdf_path).is_file():
        return FileResponse(
            outcome.pdf_path, media_type="application/pdf", filename=filename
        )
    error = GenerationError(
        offer_id=offer_id,
        kind=kind,
        error=outcome.error or "PDF introuvable après génération",
    )
    return JSONResponse(status_code=502, content=error.model_dump())


@router.post("/{offer_id}/generate/cv", response_model=None)
def generate_cv(
    offer_id: str,
    service: OffersService = Depends(get_offers_service),
    generator: Generator = Depends(get_generator),
) -> FileResponse | JSONResponse:
    """Génère le CV (bloquant) et renvoie le PDF, ou 502 + JSON si la génération échoue."""
    offer = _get_offer_or_404(service, offer_id)
    outcome = generator.generate_cv(offer)
    return _pdf_or_error(
        outcome, offer_id=offer_id, kind="cv", filename=f"cv-{offer_id}.pdf"
    )


@router.post("/{offer_id}/generate/letter", response_model=None)
def generate_letter(
    offer_id: str,
    service: OffersService = Depends(get_offers_service),
    generator: Generator = Depends(get_generator),
) -> FileResponse | JSONResponse:
    """Génère la lettre (bloquant) et renvoie le PDF, ou 502 + JSON si la génération échoue."""
    offer = _get_offer_or_404(service, offer_id)
    outcome = generator.generate_letter(offer)
    return _pdf_or_error(
        outcome, offer_id=offer_id, kind="letter", filename=f"lettre-{offer_id}.pdf"
    )
