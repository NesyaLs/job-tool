"""Source de rendu d'une lettre réécrite à la main (mode pro) : forme attendue et complétion.

Même rôle que documents/cv/source.py pour les templates de lettre : refuser une source inexploitable
avec un message qui pointe le champ fautif, compléter ce qui manque. Les inserts ne sont
affichés que si leur drapeau `show_*` est vrai (comportement du template).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jobtool.documents.cover_letter.schema import (
    DEFAULT_DESTINATAIRE,
    DEFAULT_SALUTATION,
)


class LetterRenderSource(BaseModel):
    """Tout ce que lisent les templates de lettre, avec des valeurs par défaut neutres.

    Sert aussi à COMPLÉTER un brouillon enregistré avant l'ajout d'un champ (titre,
    destinataire, salutation) : le template lit la clé sans avoir à prévoir son absence.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    nom: str = ""
    coordonnees: list[str] = Field(default_factory=list)
    date: str = ""
    lieu: str = ""
    entreprise: str = ""
    titre: str = ""
    destinataire: str = DEFAULT_DESTINATAIRE
    salutation: str = DEFAULT_SALUTATION
    objet: str = ""
    paragraphe_vous: str = ""
    paragraphe_moi: str = ""
    paragraphe_nous: str = ""
    formule: str = ""
    show_interet: bool = False
    insert_interet: str = ""
    show_projet: bool = False
    insert_projet: str = ""


def normalize_letter_source(data: dict) -> dict:
    """Source validée et complétée. Lève pydantic.ValidationError si inexploitable."""
    return LetterRenderSource.model_validate(data).model_dump()
