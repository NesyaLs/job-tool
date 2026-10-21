"""Preuve SANS clé API du câblage de BOUT EN BOUT de la lettre.

Stub du client (patch `call_structured` aiguillé routing/draft + `make_client` passe1/2). Le
reste est RÉEL : assemblage CoverLetterRender + rendu Typst -> PDF + contrôles PDF. On compare
le TEXTE EXTRAIT (pdftotext), JAMAIS les octets (timestamp Typst). ids de tests/fixtures/profil_demo.yaml.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from jobtool.core.rendering.pdf_checks import count_pdf_pages, extract_pdf_text
from jobtool.documents.cover_letter.generate import generate_cover_letter
from jobtool.documents.cover_letter.render import render_cover_letter
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)

FIXED_DAY = date(2026, 6, 15)  # date reproductible -> « 15 juin 2026 »
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]+\]")

ROUTING_GEN = CoverLetterRoutingOutput(
    entreprise="DataNova",
    domaine="ingénierie de la donnée",
    mode="generique",
    projets_ids=[],
    interets_ids=[],
)
DRAFT_GEN = CoverLetterDraftOutput(
    paragraphe_vous="Votre poste en ingénierie de la donnée chez DataNova recoupe ce que je cherche à faire après mes études.",
    insert_interet=None,
    paragraphe_moi="Diplômé ingénieur en informatique, j'ai surtout travaillé sur des problèmes où la rigueur et la performance comptent.",
    insert_projet=None,
    paragraphe_nous="Je serais heureux d'en discuter lors d'un entretien.",
    formule="Cordialement,",
)
ROUTING_CIBLE = CoverLetterRoutingOutput(
    entreprise="HPC Systems",
    domaine="calcul haute performance",
    mode="cible",
    projets_ids=["projet-noyaux-hpc.1", "projet-noyaux-hpc.2", "projet-noyaux-hpc.3"],
    interets_ids=["Contributions à des logiciels open source"],
)
DRAFT_CIBLE = CoverLetterDraftOutput(
    paragraphe_vous="Le poste en calcul haute performance chez HPC Systems correspond à ce sur quoi je veux travailler.",
    insert_interet="Je contribue à des projets open source, ce qui me garde proche des questions de performance.",
    paragraphe_moi="Diplômé ingénieur en informatique, je suis à l'aise avec les problèmes de performance et de parallélisme.",
    insert_projet="J'ai écrit une version séquentielle de référence de deux noyaux de calcul, puis je les ai parallélisés avec OpenMP et MPI avant de les porter sur GPU CUDA.",
    paragraphe_nous="Je serais heureux d'échanger sur ce poste lors d'un entretien.",
    formule="Cordialement,",
)


def run_pipeline(base, routing, draft):
    """Exécute le VRAI pipeline (assemblage + structure) avec un client LLM stubé."""

    def fake_call_structured(client, messages, response_format, **kwargs):
        if response_format is CoverLetterRoutingOutput:
            return routing
        if response_format is CoverLetterDraftOutput:
            return draft
        raise AssertionError(f"schéma inattendu dans le stub : {response_format}")

    with (
        patch(
            "jobtool.core.llm.correction_loop.call_structured",
            side_effect=fake_call_structured,
        ),
        patch(
            "jobtool.documents.cover_letter.passe1.make_client",
            side_effect=lambda: object(),
        ),
        patch(
            "jobtool.documents.cover_letter.passe2.make_client",
            side_effect=lambda: object(),
        ),
    ):
        return generate_cover_letter(base, "OFFRE STUB", today=FIXED_DAY)


def test_generique_no_inserts(moi_base, typst_out_dir):
    result = run_pipeline(moi_base, ROUTING_GEN, DRAFT_GEN)
    assert result.routing_issues == [], "passe 1 convergée (0 violation)"
    assert result.draft_issues == [], "passe 2 convergée (0 violation)"
    assert not result.render.show_interet and not result.render.show_projet, (
        "aucun bloc conditionnel activé"
    )

    pdf = str(typst_out_dir / "lettre_generique.pdf")
    render_cover_letter(result.render, pdf)
    text = extract_pdf_text(pdf)
    assert count_pdf_pages(pdf) == 1, "PDF générique = 1 page"
    assert bool(text.strip()), "texte extractible (ATS)"
    assert "Camille Martin" in text, "en-tête : nom présent"
    assert "DataNova" in text, "en-tête : entreprise présente"
    assert "15 juin 2026" in text, "en-tête : date du jour présente"
    assert "CUDA" not in text and "open source" not in text, (
        "TEXTE EXTRAIT ne contient AUCUN insert (générique)"
    )
    assert not _PLACEHOLDER_RE.search(text), "aucun placeholder [..] résiduel"


def test_cible_with_inserts(moi_base, typst_out_dir):
    result = run_pipeline(moi_base, ROUTING_CIBLE, DRAFT_CIBLE)
    assert result.routing_issues == [], "passe 1 convergée (0 violation)"
    assert result.draft_issues == [], "passe 2 convergée (0 violation)"
    assert result.render.show_interet and result.render.show_projet, (
        "blocs conditionnels activés"
    )

    pdf = str(typst_out_dir / "lettre_cible.pdf")
    render_cover_letter(result.render, pdf)
    text = extract_pdf_text(pdf)
    assert count_pdf_pages(pdf) == 1, "PDF ciblé = 1 page"
    assert bool(text.strip()), "texte extractible (ATS)"
    assert "Camille Martin" in text, "en-tête : nom présent"
    assert "Grenoble" in text, "en-tête : lieu (Grenoble) présent"
    assert "15 juin 2026" in text, "en-tête : date du jour présente"
    assert "HPC Systems" in text, "en-tête : entreprise présente"
    assert "CUDA" in text, "TEXTE EXTRAIT contient l'insert PROJET (CUDA)"
    assert "open source" in text, (
        "TEXTE EXTRAIT contient l'insert INTÉRÊT (open source)"
    )
    assert not _PLACEHOLDER_RE.search(text), "aucun placeholder [..] résiduel"
