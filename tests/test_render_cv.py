"""Preuve de rendu CV : base + tailored.example.json -> PDF (SANS clé API, aucun LLM).

On réutilise l'exemple (cohérent avec la base) pour ne pas dépendre du LLM. Après rendu, on
vérifie la tenue sur UNE page (propriété mesurée par le code) et l'extractibilité ATS (nom +
fragment de chaque bullet). Le texte extrait est comparé, jamais les octets.
"""

from __future__ import annotations

import pytest

from jobtool.core.rendering.pdf_checks import (
    check_pdf_ats,
    check_single_page,
    count_pdf_pages,
)
from jobtool.documents.cv.render import render_cv


@pytest.mark.parametrize("template", ["classic", "compact"])
def test_example_cv_renders_one_page_and_ats(
    template, example_base, example_cv, typst_out_dir, expected_ats_strings
):
    out_path = str(typst_out_dir / f"cv_demo_{template}.pdf")
    pdf_path = render_cv(example_base, example_cv, out_path, template=template)

    assert count_pdf_pages(pdf_path) == 1, "le CV doit tenir sur une seule page"
    assert check_single_page(pdf_path) == [], (
        "contrôle de page : aucune violation attendue"
    )
    missing = check_pdf_ats(pdf_path, expected_ats_strings(example_base, example_cv))
    assert missing == [], f"ATS : fragments non retrouvés dans le PDF : {missing}"


def test_example_cv_fills_basic_to_one_page_and_ats(
    example_base, example_cv, typst_out_dir, expected_ats_strings
):
    """basic : l'exemple rendu TEL QUEL déborde (en-têtes d'entrée sur deux lignes).

    C'est le cas d'usage réel : la génération passe toujours par le remplissage mesuré
    (cv/fill.py), qui garde ce qui tient sur la page. On prouve donc le même contrat que
    pour classic/compact — 1 page, texte extractible — sur le CV REMPLI.
    """
    from jobtool.documents.cv.fill import fill_to_one_page
    from jobtool.documents.cv.profiles import get_profile

    # basic affiche l'accroche : la base d'exemple n'en a pas, on lui en donne une fixe.
    profile = example_base.profile.model_copy(
        update={
            "accroche": "Étudiant ingénieur, à la recherche d'un stage de fin d'études."
        }
    )
    base = example_base.model_copy(update={"profile": profile})

    filled, report = fill_to_one_page(
        base, example_cv, profile=get_profile("basic"), template="basic"
    )
    assert not report.floor_overflow, "le plancher doit tenir sur une page"

    out_path = str(typst_out_dir / "cv_demo_basic.pdf")
    pdf_path = render_cv(base, filled, out_path, template="basic")

    assert count_pdf_pages(pdf_path) == 1
    missing = check_pdf_ats(pdf_path, expected_ats_strings(base, filled))
    assert missing == [], f"ATS : fragments non retrouvés dans le PDF : {missing}"


# --- Polices embarquées ------------------------------------------------------------


def test_embedded_fonts_cover_every_family_the_templates_need():
    """Les polices exigées par les templates sont EMBARQUÉES, pas empruntées au poste.

    `--ignore-system-fonts` isole le rendu des polices installées : une version
    différente d'une même famille déplacerait les coupures de ligne, donc la tenue sur
    une page que fill.py mesure. Ce test tombe si un fichier de police disparaît du
    paquet — le symptôme serait sinon un CV aux icônes manquantes, sans erreur.
    """
    import subprocess

    from jobtool.core.rendering.typst_renderer import FONTS_DIR

    assert FONTS_DIR.is_dir(), f"dossier de polices absent : {FONTS_DIR}"
    listing = subprocess.run(
        ["typst", "fonts", "--font-path", str(FONTS_DIR), "--ignore-system-fonts"],
        capture_output=True,
        text=True,
        check=True,
    )
    disponibles = set(listing.stdout.splitlines())
    requises = {
        "Font Awesome 6 Brands",
        "Font Awesome 6 Free",
        "Font Awesome 6 Free Solid",
        "Font Awesome 7 Brands",
        "Font Awesome 7 Free",
        "Font Awesome 7 Free Solid",
        "Fira Sans",
        "Inter",
        "Noto Sans",
        "Noto Serif",
        "Roboto",
        "Source Sans 3",
    }
    assert requises <= disponibles, (
        f"polices absentes du paquet : {sorted(requises - disponibles)}"
    )


def test_typst_is_always_invoked_with_the_embedded_fonts():
    """Aucun chemin d'appel à Typst ne peut oublier les polices embarquées.

    Les deux points d'entrée (`render_typst` pour la CLI, `_run_typst` pour le rendu
    isolé et `typst eval`) doivent porter les mêmes drapeaux : si `eval` voyait d'autres
    polices que `compile`, les boîtes relevées décriraient une autre mise en page que
    le PDF, et les zones cliquables de la retouche seraient décalées.
    """
    from pathlib import Path

    source = Path("src/jobtool/core/rendering/typst_renderer.py").read_text(
        encoding="utf-8"
    )
    assert source.count("_FONT_ARGS") >= 3, (
        "défini puis utilisé aux deux points d'appel"
    )
    assert '"--ignore-system-fonts"' in source
