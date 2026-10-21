"""Registry des sources d'agrégation : nom -> fabrique de Connector.

Centralise le CÂBLAGE (jusqu'ici inline dans la CLI) pour qu'ajouter une source ne touche
plus la CLI : on enregistre une fabrique ici, et `--source` la sélectionne par son nom. Chaque
fabrique reçoit un `BuildContext` (settings + config SmartRecruiters + debug) et renvoie
`(connector | None, pre_errors)` :
  - `None` -> la source est ignorée (identifiants absents) ; `pre_errors` l'explique ;
  - certaines sources s'AUTO-ISOLENT (Adzuna sans clés, SmartRecruiters sans entreprise) :
    elles renvoient quand même un connecteur, qui notera l'erreur à l'exécution.
Le sens des dépendances est respecté : ce module (domaine aggregator) importe core/settings,
jamais services/ ni cli/. Les imports restent légers à l'échelle du paquet (requests via les
connecteurs) ; la CLI importe ce module de façon différée pour garder `--help` instantané.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jobtool.aggregator.adzuna.connector import AdzunaConnector
from jobtool.aggregator.connector import Connector
from jobtool.aggregator.francetravail.auth import FranceTravailAuth
from jobtool.aggregator.francetravail.connector import FranceTravailConnector
from jobtool.aggregator.models import (
    CollectionConfig,
    SmartRecruitersConfig,
    WorkdayConfig,
)
from jobtool.aggregator.smartrecruiters.connector import SmartRecruitersConnector
from jobtool.aggregator.workday.connector import WorkdayConnector
from jobtool.settings import AggregatorSettings


@dataclass(frozen=True)
class BuildContext:
    """Tout ce dont une fabrique peut avoir besoin (chaque fabrique pioche ce qui la concerne)."""

    settings: AggregatorSettings
    smartrecruiters: SmartRecruitersConfig
    workday: WorkdayConfig
    debug: bool = False


# Une fabrique : contexte -> (connecteur ou None si non configuré, messages d'erreur préalables).
Builder = Callable[[BuildContext], tuple[Connector | None, list[str]]]


def _build_france_travail(ctx: BuildContext) -> tuple[Connector | None, list[str]]:
    s = ctx.settings
    if s.france_travail_client_id and s.france_travail_client_secret:
        auth = FranceTravailAuth(
            s.france_travail_client_id,
            s.france_travail_client_secret,
            s.france_travail_scope,
        )
        return FranceTravailConnector(auth, debug=ctx.debug), []
    return None, [
        "France Travail ignoré : identifiants absents (Réglages > Clés d'API, ou "
        "FRANCE_TRAVAIL_CLIENT_ID / _CLIENT_SECRET dans le .env)"
    ]


def _build_adzuna(ctx: BuildContext) -> tuple[Connector | None, list[str]]:
    # AdzunaConnector s'auto-isole si les clés manquent (note une erreur, renvoie []).
    s = ctx.settings
    return AdzunaConnector(s.adzuna_app_id, s.adzuna_app_key, debug=ctx.debug), []


def _build_smartrecruiters(ctx: BuildContext) -> tuple[Connector | None, list[str]]:
    # SmartRecruitersConnector s'auto-isole si `companies` est vide (note une erreur, renvoie []).
    sr = ctx.smartrecruiters
    return (
        SmartRecruitersConnector(
            sr.companies,
            country=sr.country,
            region=sr.region,
            city=sr.city,
            department=sr.department,
            max_per_company=sr.max_per_company,
            debug=ctx.debug,
        ),
        [],
    )


def _build_workday(ctx: BuildContext) -> tuple[Connector | None, list[str]]:
    # WorkdayConnector s'auto-isole si `sites` est vide (note une erreur, renvoie []).
    wd = ctx.workday
    return (
        WorkdayConnector(
            wd.sites,
            country=wd.country,
            max_per_site=wd.max_per_site,
            debug=ctx.debug,
        ),
        [],
    )


# Registre nom -> fabrique. Ajouter une source = ajouter une entrée ici (et son module).
SOURCE_BUILDERS: dict[str, Builder] = {
    "france_travail": _build_france_travail,
    "adzuna": _build_adzuna,
    "smartrecruiters": _build_smartrecruiters,
    "workday": _build_workday,
}

ALL_SOURCES: tuple[str, ...] = tuple(SOURCE_BUILDERS)


def enabled_sources(config: CollectionConfig) -> list[str]:
    """Sources ACTIVÉES dans la config, dans l'ordre du registre.

    C'est la sélection par défaut d'une collecte : une source se désactive dans le
    config.yaml (`sources: {adzuna: {enabled: false}}`), pas en oubliant de la demander.
    """
    return [name for name in ALL_SOURCES if config.source(name).enabled]


def build_connectors(
    names: list[str], ctx: BuildContext
) -> tuple[list[Connector], list[str]]:
    """Construit les connecteurs des sources demandées (ordre préservé). Renvoie (connecteurs, erreurs).

    Un nom inconnu lève KeyError (programmation) : la CLI ne passe que des noms validés par l'enum.
    """
    connectors: list[Connector] = []
    pre_errors: list[str] = []
    for name in names:
        connector, errors = SOURCE_BUILDERS[name](ctx)
        if connector is not None:
            connectors.append(connector)
        pre_errors.extend(errors)
    return connectors, pre_errors
