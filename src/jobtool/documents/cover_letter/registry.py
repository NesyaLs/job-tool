"""Registre des templates de lettre : nom lisible -> fichier `.typ`, et leur contrat commun.

Un nom inconnu est une ERREUR, jamais un repli : contrairement au CV historique, aucun
chemin `.typ` arbitraire n'est accepté. Le nom est stocké dans `documents.template` ; les
brouillons d'avant le registre y portent `""`, et `"classic"` était le défaut annoncé —
les deux désignent le template historique `sobre`.

CONTRAT commun, que tout template de ce registre respecte (vérifié par
tests/test_letter_templates.py sur chaque entrée) :
  - lire `render_input.json` (forme : cover_letter/source.LetterRenderSource) ;
  - importer `_edit.typ` et déclarer EXACTEMENT les zones `LETTER_FIELD_IDS`
    (`objet` seulement quand il est non vide) ;
  - concaténer les inserts comme lettre.typ : `paragraphe_vous` (+ " " + insert_interet
    si show_interet), `paragraphe_moi` (+ " " + insert_projet si show_projet) — sinon
    drafts/overrides.py désactiverait l'insert d'un texte qui ne le contient pas ;
  - enregistrer dans `field` le texte BRUT des données (un habillage typographique passe
    par `body:`) : les textes d'origine restent valables d'un template à l'autre ;
  - imprimer `nom`, `entreprise` et `date` d'un seul tenant (contrôle ATS).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

TEMPLATES_DIR = Path(str(files("jobtool.documents.cover_letter").joinpath("templates")))


class LetterTemplateError(ValueError):
    """Nom de template de lettre inconnu."""


@dataclass(frozen=True)
class LetterTemplateSpec:
    """Un template de lettre proposé : nom stocké, libellé, description, fichier."""

    name: str
    label: str
    description: str
    filename: str


LETTER_TEMPLATES: dict[str, LetterTemplateSpec] = {
    "sobre": LetterTemplateSpec(
        name="sobre",
        label="Sobre",
        description="Une colonne, en-tête discret : la mise en page historique.",
        filename="lettre.typ",
    ),
    "moderne": LetterTemplateSpec(
        name="moderne",
        label="Moderne",
        description="En-tête avec titre et coordonnées, destinataire et date en regard.",
        filename="lettre_moderne.typ",
    ),
}

#: Template utilisé quand rien n'est précisé.
DEFAULT_LETTER_TEMPLATE = "sobre"

#: Anciennes valeurs stockées qui désignent le template historique.
LEGACY_LETTER_TEMPLATES: dict[str, str] = {"": "sobre", "classic": "sobre"}

#: Zones retouchables que chaque template déclare par `field(...)`.
LETTER_FIELD_IDS: tuple[str, ...] = (
    "objet",
    "paragraphe_vous",
    "paragraphe_moi",
    "paragraphe_nous",
    "formule",
)


def canonical_letter_template(name: str) -> str:
    """Nom canonique d'un template (ancien nom compris) ; LetterTemplateError si inconnu."""
    name = LEGACY_LETTER_TEMPLATES.get(name, name)
    if name not in LETTER_TEMPLATES:
        raise LetterTemplateError(
            f"template de lettre inconnu : {name!r} "
            f"(attendu : {' | '.join(LETTER_TEMPLATES)})"
        )
    return name


def letter_template_path(name: str) -> Path:
    """Fichier `.typ` d'un template (nom, ancien nom ou `""`) ; LetterTemplateError sinon."""
    return TEMPLATES_DIR / LETTER_TEMPLATES[canonical_letter_template(name)].filename


__all__ = [
    "DEFAULT_LETTER_TEMPLATE",
    "LEGACY_LETTER_TEMPLATES",
    "LETTER_FIELD_IDS",
    "LETTER_TEMPLATES",
    "TEMPLATES_DIR",
    "LetterTemplateError",
    "LetterTemplateSpec",
    "canonical_letter_template",
    "letter_template_path",
]
