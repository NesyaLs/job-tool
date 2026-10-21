"""Lecture du fichier de config de l'agrégateur (critères de collecte par défaut).

Format YAML simple, édité à la main par l'utilisateur. Aucun secret ici (les identifiants
viennent du .env). Tout champ absent retombe sur le défaut de SearchCriteria.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from jobtool.aggregator.models import (
    CollectionConfig,
    SearchCriteria,
    SmartRecruitersConfig,
    SourceSettings,
    WorkdayConfig,
)


class ConfigError(Exception):
    """Config d'agrégation illisible (fichier absent, YAML cassé, valeurs invalides).

    Exception APPLICATIVE (hérite d'Exception, jamais SystemExit) : attrapable par les
    couches d'interface, qui la traduisent (typer.Exit côté CLI, HTTPException côté API).
    """


def load_criteria(path: str | Path) -> SearchCriteria:
    """Charge les critères depuis le YAML ; lève ConfigError (message clair) si invalide."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"Erreur : config introuvable : {p}\n"
            "Crée data/aggregator/config.yaml (voir l'exemple commenté du projet)."
        )
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Erreur : config YAML mal formée dans {p}\n{exc}")

    section = data.get("criteres", data) if isinstance(data, dict) else {}
    try:
        return _criteria(section)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Erreur : critères invalides dans {p} : {exc}")


def _criteria(section: dict, defaults: dict | None = None) -> SearchCriteria:
    """Une recherche du YAML -> SearchCriteria ; `defaults` complète les champs absents.

    `motsCles` (graphie de l'API France Travail, historique du fichier) et `mots_cles`
    sont acceptés tous les deux.
    """
    merged = {**(defaults or {}), **section}
    return SearchCriteria(
        mots_cles=merged.get("motsCles", merged.get("mots_cles", "")) or "",
        commune=merged.get("commune"),
        rayon=merged.get("rayon"),
        departement=merged.get("departement"),
        max_results=int(merged.get("max_results", 150)),
        min_creation_date=merged.get("min_creation_date"),
        max_creation_date=merged.get("max_creation_date"),
    )


def _read_yaml(p: Path) -> dict:
    """Contenu du YAML (dict vide si le fichier est vide) ; ConfigError si illisible."""
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"Erreur : config introuvable : {p}\n"
            "Crée data/aggregator/config.yaml (voir l'exemple commenté du projet)."
        )
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Erreur : config YAML mal formée dans {p}\n{exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"Erreur : la config {p} doit être un dictionnaire YAML")
    return data


def load_collection_config(
    path: str | Path, known_sources: tuple[str, ...] | None = None
) -> CollectionConfig:
    """Charge TOUT ce qu'une collecte lit : recherches, réglages par source, ATS.

    Recherches : la liste `recherches:` si elle existe ; sinon la section historique
    `criteres:`, qui devient une recherche unique. Les champs de `criteres:` servent de
    valeurs par défaut aux entrées de `recherches:` (ex. `max_results` commun).

    `known_sources` : noms acceptés dans la section `sources:`. Une faute de frappe
    (« smartrecruiter ») y serait sinon ignorée en silence, et la source jamais désactivée.
    """
    p = Path(path)
    data = _read_yaml(p)
    base = data.get("criteres") or {}
    if not isinstance(base, dict):
        raise ConfigError(f"Erreur : section `criteres` invalide dans {p}")

    raw_searches = data.get("recherches")
    try:
        if raw_searches:
            if not isinstance(raw_searches, list) or not all(
                isinstance(s, dict) for s in raw_searches
            ):
                raise ConfigError(
                    f"Erreur : `recherches` doit être une liste de dictionnaires ({p})"
                )
            searches = [_criteria(s, base) for s in raw_searches]
        else:
            searches = [_criteria(base)]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Erreur : recherches invalides dans {p} : {exc}")

    raw_sources = data.get("sources") or {}
    if not isinstance(raw_sources, dict):
        raise ConfigError(f"Erreur : section `sources` invalide dans {p}")
    unknown = sorted(set(raw_sources) - set(known_sources or raw_sources))
    if unknown:
        raise ConfigError(
            f"Erreur : source(s) inconnue(s) dans {p} : {', '.join(unknown)} "
            f"(connues : {', '.join(known_sources or ())})"
        )
    try:
        sources = {
            str(name): SourceSettings(**(settings or {}))
            for name, settings in raw_sources.items()
        }
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Erreur : section `sources` invalide dans {p} : {exc}")

    return CollectionConfig(
        searches=searches,
        sources=sources,
        smartrecruiters=load_smartrecruiters_config(p),
        workday=load_workday_config(p),
    )


def load_smartrecruiters_config(path: str | Path) -> SmartRecruitersConfig:
    """Charge la section `smartrecruiters:` du YAML ; absente/vide -> config vide (source ignorée).

    Ne plante PAS si le fichier ou la section manque : la source SmartRecruiters s'auto-isole
    alors (companies vide). `companies` accepte une liste OU une chaîne « a, b » ; `department`
    est ramené en str (ID numérique). Une YAML mal formée reste une erreur claire (ConfigError).
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return SmartRecruitersConfig()
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Erreur : config YAML mal formée dans {p}\n{exc}")

    section = data.get("smartrecruiters", {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        section = {}

    companies = section.get("companies") or []
    if isinstance(companies, str):
        companies = [c.strip() for c in companies.split(",") if c.strip()]

    department = section.get("department")
    try:
        return SmartRecruitersConfig(
            companies=[str(c) for c in companies],
            country=section.get("country"),
            region=section.get("region"),
            city=section.get("city"),
            department=str(department) if department is not None else None,
            max_per_company=section.get("max_per_company"),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Erreur : section smartrecruiters invalide dans {p} : {exc}")


def load_workday_config(path: str | Path) -> WorkdayConfig:
    """Charge la section `workday:` du YAML ; absente/vide -> config vide (source ignorée).

    Ne plante PAS si le fichier ou la section manque : la source Workday s'auto-isole alors
    (sites vide). `sites` accepte une liste OU une chaîne « url1, url2 ». Une YAML mal formée
    reste une erreur claire (ConfigError), comme pour les autres sections.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return WorkdayConfig()
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Erreur : config YAML mal formée dans {p}\n{exc}")

    section = data.get("workday", {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        section = {}

    sites = section.get("sites") or []
    if isinstance(sites, str):
        sites = [s.strip() for s in sites.split(",") if s.strip()]

    try:
        return WorkdayConfig(
            sites=[str(s) for s in sites],
            country=section.get("country"),
            max_per_site=section.get("max_per_site"),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Erreur : section workday invalide dans {p} : {exc}")
