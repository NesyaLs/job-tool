"""Démo/preuve de rendu de lettre de motivation (SANS clé API, aucun LLM).

Construit 2 cas stubés EN DUR (générique / ciblé), rend les PDF, puis prouve via
core/rendering/pdf_checks : 1 page ; texte EXTRACTIBLE (ATS) ; effet des blocs conditionnels
sur le TEXTE EXTRAIT (jamais les octets : Typst embarque un timestamp) :
  - cas A « générique » : marqueurs stub ABSENTS ;
  - cas B « ciblé »     : les deux marqueurs stub PRÉSENTS.
"""

from __future__ import annotations

import pytest

from jobtool.core.rendering.pdf_checks import count_pdf_pages, extract_pdf_text
from jobtool.documents.cover_letter.render import render_cover_letter
from jobtool.documents.cover_letter.schema import CoverLetterRender

# Marqueurs stub reconnaissables, pour prouver l'effet des blocs conditionnels.
INTERET_STUB = "INSERT_INTERET_STUB"
PROJET_STUB = "INSERT_PROJET_STUB"


def _base_fields(**override) -> dict:
    """Champs communs stubés (paragraphes français plausibles, en-tête sans poste)."""
    fields = dict(
        nom="Lucas Moreau",
        coordonnees=["lucas.moreau@example.com", "+33 6 12 34 56 78", "Lyon, France"],
        date="12 juin 2026",
        lieu="Lyon",
        entreprise="FinUp Technologies",
        objet=None,
        paragraphe_vous=(
            "Votre entreprise développe des solutions de paiement à fort trafic et "
            "place la fiabilité de ses services au cœur de son activité."
        ),
        paragraphe_moi=(
            "En dernière année d'école d'ingénieur, j'ai conçu et mis en production des "
            "API robustes et bien testées lors de mes stages et projets."
        ),
        paragraphe_nous=(
            "Rejoindre vos équipes me permettrait de contribuer à des services exigeants "
            "tout en continuant à progresser à vos côtés."
        ),
        formule=(
            "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations "
            "distinguées."
        ),
    )
    fields.update(override)
    return fields


def case_generique() -> CoverLetterRender:
    return CoverLetterRender(**_base_fields(show_interet=False, show_projet=False))


def case_cible() -> CoverLetterRender:
    return CoverLetterRender(
        **_base_fields(
            show_interet=True,
            insert_interet=INTERET_STUB,
            show_projet=True,
            insert_projet=PROJET_STUB,
        )
    )


@pytest.mark.parametrize(
    "label, letter_factory, expect_markers",
    [
        ("generique", case_generique, False),
        ("cible", case_cible, True),
    ],
)
def test_render_conditional_blocks(
    label, letter_factory, expect_markers, typst_out_dir
):
    letter = letter_factory()
    out_pdf = typst_out_dir / f"lettre_{label}.pdf"
    render_cover_letter(letter, str(out_pdf))

    assert count_pdf_pages(str(out_pdf)) == 1, "1 page attendue"

    text = extract_pdf_text(str(out_pdf))
    flat = " ".join(text.split())
    assert flat.strip(), "texte non extractible (PDF vide à l'extraction)"
    assert letter.nom in flat, (
        f"nom de l'expéditeur ({letter.nom!r}) introuvable dans l'extraction"
    )

    # Effet des blocs conditionnels, prouvé sur le TEXTE EXTRAIT (jamais les octets).
    assert (INTERET_STUB in flat) is expect_markers, (
        "marqueur INTÉRÊT incohérent avec le flag conditionnel"
    )
    assert (PROJET_STUB in flat) is expect_markers, (
        "marqueur PROJET incohérent avec le flag conditionnel"
    )
