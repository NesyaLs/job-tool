"""Rendu d'une lettre de motivation : injecte le modèle dans le template choisi et compile.

Réutilise core/rendering/typst_renderer (AUCUNE réécriture du moteur de rendu). Pas de
troncage ni de mesure ici : on assemble le dict de données puis on délègue la compilation.
Le choix du fichier `.typ` appartient au registre (cover_letter/registry.py).
"""

from __future__ import annotations

from jobtool.core.rendering.typst_renderer import render_typst
from jobtool.documents.cover_letter.registry import (
    DEFAULT_LETTER_TEMPLATE,
    TEMPLATES_DIR,
    letter_template_path,
)
from jobtool.documents.cover_letter.schema import CoverLetterRender

# Chemin du template historique, conservé pour les appelants existants.
TEMPLATE_PATH = TEMPLATES_DIR / "lettre.typ"


def _build_render_input(letter: CoverLetterRender) -> dict:
    """Assemble le dict consommé par les templates de lettre.

    `objet` None -> "" : le template ne manipule que des chaînes (test `!= ""`).
    """
    return {
        "nom": letter.nom,
        "coordonnees": list(letter.coordonnees),
        "date": letter.date,
        "lieu": letter.lieu,
        "entreprise": letter.entreprise,
        "titre": letter.titre,
        "destinataire": letter.destinataire,
        "salutation": letter.salutation,
        "objet": letter.objet or "",
        "paragraphe_vous": letter.paragraphe_vous,
        "paragraphe_moi": letter.paragraphe_moi,
        "paragraphe_nous": letter.paragraphe_nous,
        "formule": letter.formule,
        "show_interet": letter.show_interet,
        "insert_interet": letter.insert_interet,
        "show_projet": letter.show_projet,
        "insert_projet": letter.insert_projet,
    }


def build_render_input(letter: CoverLetterRender) -> dict:
    """Données de rendu d'une lettre (façade publique de `_build_render_input`).

    Exposée pour la retouche manuelle, au même titre que son homologue CV : le brouillon
    éditable est ce dict, recompilable tel quel sans repasser par le LLM.
    """
    return _build_render_input(letter)


def render_cover_letter(
    letter: CoverLetterRender,
    out_path: str,
    template: str = DEFAULT_LETTER_TEMPLATE,
) -> str:
    """Rend la lettre en PDF à out_path avec le template nommé ; retourne le chemin.

    Lève registry.LetterTemplateError sur un nom inconnu (aucun chemin arbitraire).
    """
    return render_typst(
        letter_template_path(template), _build_render_input(letter), str(out_path)
    )
