"""Application des retouches manuelles à un `render_input`, par type de document.

Une retouche est un couple {id de champ -> texte}. Ce module est le SEUL endroit qui sait
où chaque id vit dans le dict de rendu ; il ne modifie jamais l'original (copie profonde),
pour que « revenir au texte généré » reste toujours possible.

Les ids de champs sont exactement ceux que les templates déclarent via `field(...)` :
  - CV     : l'id de TRAÇABILITÉ de chaque puce (l'achievement reformulé), plus
             `hard_skills`, `soft_skills`, `interests`, `accroche` ;
  - lettre : `objet`, `paragraphe_vous`, `paragraphe_moi`, `paragraphe_nous`, `formule`
             (cover_letter/registry.LETTER_FIELD_IDS, contrat commun à tous ses templates).
"""

from __future__ import annotations

import copy

from jobtool.documents.drafts.kinds import RenderInput
from jobtool.documents.drafts.models import DocumentKind

# Lignes de compétences : le template les recompose avec ce séparateur. Une ligne
# retouchée est donc redécoupée dessus — l'opération est neutre au rendu, puisque
# join(split(ligne)) redonne la ligne exacte.
SKILLS_SEPARATOR = ", "

_CV_LIST_FIELDS = ("hard_skills", "soft_skills", "interests")
_CV_TEXT_FIELDS = ("accroche",)

_LETTER_TEXT_FIELDS = ("objet", "paragraphe_nous", "formule")
# Paragraphes dont le template concatène un insert conditionnel : le champ éditable porte
# le texte DÉJÀ composé, donc une retouche doit désactiver l'insert (sinon il serait
# ajouté une seconde fois derrière le texte que l'humain a écrit).
_LETTER_COMPOSED = {
    "paragraphe_vous": "show_interet",
    "paragraphe_moi": "show_projet",
}


def apply_cv(data: RenderInput, overrides: dict[str, str]) -> RenderInput:
    """Applique les retouches d'un CV : puces tracées, accroche, lignes de compétences."""
    for experience in data.get("experiences", []):
        for bullet in experience.get("bullets", []):
            if bullet.get("id") in overrides:
                bullet["text"] = overrides[bullet["id"]]

    for name in _CV_TEXT_FIELDS:
        if name in overrides:
            data[name] = overrides[name]

    for name in _CV_LIST_FIELDS:
        if name in overrides:
            line = overrides[name].strip()
            data[name] = line.split(SKILLS_SEPARATOR) if line else []

    return data


def apply_letter(data: RenderInput, overrides: dict[str, str]) -> RenderInput:
    """Applique les retouches d'une lettre : paragraphes, objet, formule."""
    for name in _LETTER_TEXT_FIELDS:
        if name in overrides:
            data[name] = overrides[name]

    for name, insert_flag in _LETTER_COMPOSED.items():
        if name in overrides:
            data[name] = overrides[name]
            data[insert_flag] = False

    return data


def apply_overrides(
    kind: DocumentKind, render_input: RenderInput, overrides: dict[str, str]
) -> RenderInput:
    """Retourne une COPIE de `render_input` où les champs retouchés portent le texte saisi.

    Les ids inconnus sont ignorés en silence : un brouillon régénéré peut avoir perdu une
    puce, et une retouche orpheline ne doit pas empêcher le document de se recompiler.

    Le choix de QUELLE application appartient à `kinds.py`, avec les trois autres questions
    que le type de document commande.
    """
    from jobtool.documents.drafts.kinds import policy_for

    data = copy.deepcopy(render_input)
    if not overrides:
        return data
    return policy_for(kind).apply_overrides(data, overrides)
