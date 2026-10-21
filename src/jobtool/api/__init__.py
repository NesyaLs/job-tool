"""Couche d'interface HTTP (FastAPI) de jobtool.

NOUVELLE couche d'interface, au même rang que `cli/` et `ui/` : elle reçoit des requêtes,
appelle `services/`, renvoie du JSON (ou un PDF). Elle ne touche JAMAIS au stockage ni aux
domaines en direct et ne contient AUCUNE logique métier (voir project_ctx.md : sens des dépendances
cli/api → services → domaines → core/settings).
"""
