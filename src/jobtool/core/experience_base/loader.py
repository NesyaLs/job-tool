"""Chargement + validation lisible d'une base d'expériences (YAML -> ExperienceBase).

Transverse (commun CV + lettre de motivation). En cas de YAML mal formé ou de
données invalides, on lève BaseLoadError (Exception applicative, message clair)
plutôt qu'une stack trace brute — JAMAIS SystemExit : une BaseException traverserait
les `except Exception` de l'API et sortirait en 500 non géré. La traduction en
sortie (typer.Exit côté CLI, réponse HTTP côté API) appartient aux couches
d'interface.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from jobtool.core.experience_base.models import ExperienceBase


class BaseLoadError(Exception):
    """Base d'expériences illisible (fichier absent, YAML cassé, données invalides)."""


def load_base(path: Path) -> ExperienceBase:
    """Lit + parse + valide le YAML. Lève BaseLoadError avec un message déjà lisible."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BaseLoadError(f"Erreur : fichier introuvable : {path}")

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise BaseLoadError(f"Erreur : YAML mal formé dans {path}\n{exc}")

    try:
        return ExperienceBase.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"Erreur : base invalide dans {path} ({exc.error_count()} problème(s)) :"
        ]
        for err in exc.errors():
            location = ".".join(str(part) for part in err["loc"]) or "(racine)"
            lines.append(f"  - {location} : {err['msg']}")
        raise BaseLoadError("\n".join(lines))
