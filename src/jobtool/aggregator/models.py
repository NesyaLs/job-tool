"""Schémas de l'agrégateur d'offres (Pydantic) — source-agnostiques.

Vocabulaire : on utilise `native_id` (id natif côté source) et JAMAIS `source_id`, déjà
employé ailleurs dans le projet pour les achievements tracés (éviter la collision).

Flux : Connector.fetch -> list[RawJob] (brut, par source) -> normalize.raw_to_job -> Job
(unifié, dédupliqué par dedup_hash) -> SQLite + export .txt.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# Télétravail : valeurs unifiées (l'API source peut ne pas renseigner -> « inconnu »).
Remote = Literal["oui", "non", "hybride", "inconnu"]


class JobStatus(str, Enum):
    """Cycle de vie d'une offre côté utilisateur."""

    new = "new"
    seen = "seen"
    applied = "applied"
    rejected = "rejected"


class Salary(BaseModel):
    """Rémunération optionnelle (tous champs facultatifs : les sources sont incomplètes)."""

    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: str | None = None  # ex. "year", "month", "hour"


class RawJob(BaseModel):
    """Offre BRUTE produite par un Connector (clés déjà unifiées, mais NON dédupliquée).

    `raw` = payload source MINIMISÉ (aucune donnée personnelle de contact) — voir la
    minimisation côté connecteur (liste blanche de champs, jamais les contacts).
    """

    source: str
    native_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    remote: Remote = "inconnu"
    contract_type: str = ""
    salary: Salary | None = None
    description: str = ""
    url: str = ""
    posted_date: str | None = None  # ISO-8601 tel que fourni par la source
    raw: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """Offre UNIFIÉE et dédupliquée, persistée en SQLite et exportée en .txt.

    `id` interne == `dedup_hash` (identité canonique stable d'une offre, indépendante de
    la source). `native_id`/`source` tracent la PROVENANCE (une même offre peut être vue
    via plusieurs sources : voir la table job_sources du stockage).
    """

    id: str
    source: str
    native_id: str
    title: str
    company: str
    location: str
    remote: Remote = "inconnu"
    contract_type: str = ""
    salary: Salary | None = None
    description: str = ""
    url: str = ""
    posted_date: str | None = None
    fetched_date: str
    dedup_hash: str
    status: JobStatus = JobStatus.new
    favorite: bool = False  # drapeau INDÉPENDANT du statut (PAS un nouveau JobStatus)
    # Date de candidature (ISO-8601), renseignée quand le statut passe à `applied` et vidée
    # quand il en sort. Alimente l'écran « Candidatures ». None = pas (ou plus) postulé, ou
    # offre passée `applied` avant l'existence de la colonne (date inconnue, jamais inventée).
    applied_at: str | None = None
    # Date du masquage (statut `seen`), None sinon. Lue dans `user_offers.hidden_at`.
    hidden_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    # --- Champs NORMALISÉS (aggregator/geo.py, aggregator/contracts.py) -------------
    # Calculés par le code à partir des champs de la source, jamais saisis : c'est sur
    # eux que la recherche filtre et que le dédoublonnage compare, `location` et
    # `contract_type` restant le texte d'origine, affiché tel quel.
    country: str | None = None  # ISO2
    region: str | None = None
    department: str | None = None  # code INSEE (« 75 », « 2A »)
    city: str | None = None
    place_precision: str = "inconnu"  # voir geo.PRECISIONS
    contract_kind: str = "inconnu"  # voir contracts.CONTRACT_KINDS
    work_time: str | None = None  # temps_plein | temps_partiel
    # --- Catalogue (lus en base, jamais produits par une source) ----------------------
    # Liens de l'offre par source ({source, url}), le lien principal d'abord : une offre
    # dédoublonnée garde TOUS ses liens de candidature.
    links: list[dict[str, str]] = Field(default_factory=list)
    expired_at: str | None = (
        None  # plus revue par aucune collecte, gardée pour son état
    )
    private: bool = False  # saisie à la main, visible de son seul auteur


class SmartRecruitersConfig(BaseModel):
    """Config PROPRE à la source SmartRecruiters (section `smartrecruiters:` du config.yaml).

    `companies` = identifiants d'entreprise (segment d'URL carrière, ex. « visa ») : ce ne sont
    PAS des secrets -> ils vivent dans le YAML édité à la main, pas dans le .env. Liste vide ->
    la source s'auto-isole (notée, jamais fatale). Les filtres géo sont SmartRecruiters-spécifiques
    (ISO2 / region / city / department=ID numérique), non transposables depuis les codes INSEE de
    SearchCriteria — d'où leur place ici plutôt que dans les critères communs.
    """

    companies: list[str] = Field(default_factory=list)
    country: str | None = None  # code ISO2 minuscule, ex. "us"
    region: str | None = None  # code région, ex. "TX"
    city: str | None = None  # nom de ville, ex. "Austin"
    department: str | None = None  # ID NUMÉRIQUE (le label renvoie HTTP 400)
    # Plafond PAR ENTREPRISE : sans lui, la première entreprise d'une longue liste (ALTEN,
    # 650 offres en France) épuiserait le plafond de la source à elle seule.
    max_per_company: int | None = None


class WorkdayConfig(BaseModel):
    """Config PROPRE à la source Workday (section `workday:` du config.yaml).

    `sites` = URLs publiques de sites carrière Workday (ex.
    « https://kainos.wd3.myworkdayjobs.com/en-US/kainos ») : ce ne sont PAS des secrets ->
    elles vivent dans le YAML édité à la main, pas dans le .env. Le tenant / data center /
    site / locale sont DÉRIVÉS de l'URL (voir workday/urls.py) : on ne devine jamais le data
    center. Liste vide -> la source s'auto-isole (notée, jamais fatale), comme SmartRecruiters.
    """

    sites: list[str] = Field(default_factory=list)
    # Pays, par son NOM tel que Workday l'affiche (« France »). Workday ne filtre pas par
    # code ISO : il faut retrouver l'identifiant du facet `locationCountry` dans la première
    # page de résultats. Un site sans ce facet (Banque de France : tout est en France) est
    # interrogé sans filtre.
    country: str | None = None
    # Plafond PAR SITE, même raison que `SmartRecruitersConfig.max_per_company`.
    max_per_site: int | None = None


class SourceSettings(BaseModel):
    """Réglages communs à toutes les sources (section `sources:` du config.yaml).

    `max_results` : plafond de la source pour UNE collecte. Pour les sources interrogées
    par recherche (France Travail, Adzuna), il s'applique à CHAQUE recherche ; pour les
    ATS, qui ramènent tout le catalogue d'une entreprise, à l'ensemble des entreprises.
    Absent : le `max_results` de la recherche.
    """

    enabled: bool = True
    max_results: int | None = Field(default=None, ge=1)


class CollectionConfig(BaseModel):
    """Tout ce qu'une collecte lit dans le config.yaml.

    `searches` : recherches exécutées par les sources qui en acceptent (France Travail,
    Adzuna). Les ATS n'en ont pas besoin : ils ramènent les offres des entreprises listées.
    `sources` : réglages par source ; une source absente de la section est ACTIVÉE avec
    ses valeurs par défaut, pour qu'un fichier antérieur à cette section continue de
    collecter comme avant.
    """

    searches: list[SearchCriteria] = Field(default_factory=list)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)
    smartrecruiters: SmartRecruitersConfig = Field(
        default_factory=SmartRecruitersConfig
    )
    workday: WorkdayConfig = Field(default_factory=WorkdayConfig)

    def source(self, name: str) -> SourceSettings:
        """Réglages d'une source (défauts si la section ne la mentionne pas)."""
        return self.sources.get(name) or SourceSettings()


class SearchCriteria(BaseModel):
    """Critères de collecte (issus du fichier de config, ajustés par l'utilisateur).

    Filtres géographiques ALTERNATIFS (jamais envoyés ensemble à l'API) :
      - `commune` (+ `rayon`) : autour d'un point (code INSEE de commune) ;
      - `departement` : un ou plusieurs départements (codes INSEE, séparés par virgule).
    Voir `resolve_geo` pour la règle d'exclusivité déterministe.
    """

    mots_cles: str = ""
    commune: str | None = None  # CODE INSEE de la commune (PAS le code postal)
    rayon: int | None = None  # km autour de la commune
    departement: str | None = (
        None  # codes INSEE de département, ex. "75" ou "75,92,93,94"
    )
    max_results: int = 150
    min_creation_date: str | None = None  # ISO-8601
    max_creation_date: str | None = None  # ISO-8601

    def resolve_geo(self) -> tuple[dict[str, Any], str | None]:
        """Filtre géographique EXCLUSIF à envoyer à l'API + avertissement éventuel.

        Règle déterministe (commune+rayon et departement sont ALTERNATIFS, jamais ensemble) :
          1. `departement` renseigné  -> {"departement": ...} ; commune/rayon IGNORÉS ;
          2. sinon `commune` renseignée -> {"commune": ...(, "rayon": ...)} ;
          3. sinon -> {} (recherche nationale, sans filtre géographique).

        Si `commune` ET `departement` sont renseignés : `departement` PRIORITAIRE, `commune`
        ignorée, et un message d'avertissement est renvoyé (la run ne plante pas). `departement`
        est transmis TEL QUEL (la virgule pour plusieurs départements est gérée par l'API).
        """
        if self.departement:
            warning = None
            if self.commune:
                warning = (
                    "commune ET departement renseignés : departement prioritaire, "
                    "commune ignorée (filtres géographiques mutuellement exclusifs)."
                )
            return {"departement": self.departement}, warning
        if self.commune:
            geo: dict[str, Any] = {"commune": self.commune}
            if self.rayon:
                geo["rayon"] = self.rayon
            return geo, None
        return {}, None


# `CollectionConfig` cite `SearchCriteria`, défini plus bas : on résout la référence ici.
CollectionConfig.model_rebuild()
