"""Modèle d'un brouillon de document : la source de vérité d'une retouche manuelle.

Un brouillon = ce qu'il faut pour REFABRIQUER le PDF à l'identique, sans rappeler le LLM :

  - `render_input`   : le dict de rendu produit par la génération (état d'ORIGINE, figé) ;
  - `overrides`      : {id de champ -> texte réécrit à la main}, la seule chose qui bouge ;
  - `original_texts` : le texte généré de chaque champ, relevé UNE FOIS auprès du
    compilateur à la création. Figé comme `render_input`, il évite de recompiler l'état
    d'origine à chaque enregistrement juste pour proposer « revenir au texte généré ».

Le PDF n'est PAS stocké comme un état : c'est un produit DÉRIVÉ, recalculé à chaque
enregistrement. Garder l'original séparé des retouches est ce qui rend « revenir au texte
généré » possible à tout moment, champ par champ, et ce qui rend visible — pour l'humain
comme pour les contrôles — quels faits ont été réécrits à la main.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocumentKind = Literal["cv", "letter"]


class DocumentDraft(BaseModel):
    """Un document généré + ses retouches manuelles en cours."""

    doc_id: str
    offer_id: str
    kind: DocumentKind
    template: str  # nom de template CV (classic|compact|…) ; "" pour une lettre
    render_input: dict  # état d'origine, tel que produit par la génération
    original_texts: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, str] = Field(default_factory=dict)
    # Mode pro : render_input réécrit en entier à la main (None = mode normal). Il
    # REMPLACE l'entrée de compilation sans toucher à render_input ni aux overrides :
    # le quitter rend le document tel qu'il était avant.
    edited_input: dict | None = None
    created_at: str
    updated_at: str

    @property
    def edited_field_ids(self) -> set[str]:
        """Ids des champs réécrits à la main (utile aux contrôles et à l'affichage)."""
        return set(self.overrides)
