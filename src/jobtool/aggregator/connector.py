"""Interface abstraite des connecteurs de sources d'offres.

Une classe par source en hérite (ex. FranceTravailConnector). Principe d'ISOLATION du
projet : `fetch` ne doit JAMAIS lever sur une erreur réseau/HTTP — il capture, journalise
(sans secret) et renvoie les résultats PARTIELS déjà collectés. Les erreurs rencontrées
sont exposées via `errors` pour le résumé de run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from jobtool.aggregator.models import RawJob, SearchCriteria


class Connector(ABC):
    """Contrat minimal d'une source : `source` (identifiant) + `fetch(criteria)`."""

    #: identifiant court et stable de la source (ex. "france_travail").
    source: str = "base"
    #: True : la source s'interroge par RECHERCHE (mots-clés, géo) et `fetch` est appelé
    #: une fois par recherche de la config. False (ATS) : elle ramène le catalogue des
    #: entreprises configurées, et `fetch` n'est appelé qu'une fois, sans mot-clé.
    uses_search: bool = True

    def __init__(self) -> None:
        # Erreurs NON fatales accumulées pendant le dernier fetch (pour le résumé).
        self.errors: list[str] = []

    @abstractmethod
    def fetch(self, criteria: SearchCriteria) -> list[RawJob]:
        """Récupère les offres correspondant aux critères. Ne lève pas : isolation."""
        raise NotImplementedError
