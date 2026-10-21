"""Preuves du template « basic » (@preview/basic-resume) : en-tête, liens, zones éditables.

Aucun appel LLM : on rend les données d'exemple. Les propriétés vérifiées sont MESURÉES
sur ce que Typst produit (URI des liens dans le PDF, zones `field` relevées par le
compilateur), jamais déduites du template.
"""

from __future__ import annotations

import logging
import re
import subprocess
import zlib
from pathlib import Path

import pytest

from jobtool.core.rendering.typst_renderer import (
    prepare_work_dir,
    query_field_boxes,
    render_typst,
)
from jobtool.documents.cv.profiles import profile_compact
from jobtool.documents.cv.render import TEMPLATES_DIR, _split_links, build_render_input

BASIC_TEMPLATE = TEMPLATES_DIR / "cv_basic.typ"
COMPACT_TEMPLATE = TEMPLATES_DIR / "cv_compact.typ"

# --- répartition des liens ----------------------------------------------------


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        (
            ["https://github.com/moi", "https://www.linkedin.com/in/moi"],
            {
                "github": "github.com/moi",
                "linkedin": "www.linkedin.com/in/moi",
                "site": "",
            },
        ),
        # Sous-domaine quelconque (faute de frappe vue dans une vraie base).
        (
            ["https://wwww.linkedin.com/camille"],
            {"github": "", "linkedin": "wwww.linkedin.com/camille", "site": ""},
        ),
        # http:// retiré aussi ; domaine inconnu -> site.
        (
            ["http://moi.dev/portfolio"],
            {"github": "", "linkedin": "", "site": "moi.dev/portfolio"},
        ),
        # Déjà sans schéma : conservé tel quel.
        (["github.com/moi"], {"github": "github.com/moi", "linkedin": "", "site": ""}),
        # Un domaine qui CONTIENT « github.com » sans en être un sous-domaine -> site.
        (
            ["https://notgithub.com/x"],
            {"github": "", "linkedin": "", "site": "notgithub.com/x"},
        ),
        ([], {"github": "", "linkedin": "", "site": ""}),
    ],
)
def test_split_links(links, expected):
    assert _split_links(links) == expected


def test_extra_links_are_ignored_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="jobtool.documents.cv.render"):
        fields = _split_links(["https://moi.dev", "https://gitlab.com/moi"])

    assert fields["site"] == "moi.dev", "le PREMIER lien non reconnu l'emporte"
    assert "gitlab.com/moi" in caplog.text


def test_render_input_adds_separate_contact_keys(example_base, example_cv):
    profile = build_render_input(example_base, example_cv, profile_compact)["profile"]

    # Clés historiques inchangées (classic/compact ne lisent qu'elles).
    assert profile["contact"].startswith(example_base.profile.email)
    assert profile["links"] == list(example_base.profile.links or [])
    # Clés ajoutées pour basic, sans schéma.
    assert profile["email"] == example_base.profile.email
    for key in ("github", "linkedin", "site"):
        assert "://" not in profile[key]


# --- rendu --------------------------------------------------------------------


def _pdf_uris(pdf: Path) -> set[str]:
    """URI des annotations de lien d'un PDF (objets en clair ou flux compressés)."""
    raw = pdf.read_bytes()
    chunks = [raw]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    return {
        uri.decode()
        for chunk in chunks
        for uri in re.findall(rb"/URI\s*\(([^)]*)\)", chunk)
    }


def test_basic_links_never_double_the_scheme(example_base, example_cv, typst_out_dir):
    profile = example_base.profile.model_copy(
        update={
            "links": [
                "https://github.com/moi",
                "http://moi.dev",
                "https://www.linkedin.com/in/moi",
            ]
        }
    )
    base = example_base.model_copy(update={"profile": profile})
    data = build_render_input(base, example_cv, profile_compact)

    pdf = Path(render_typst(BASIC_TEMPLATE, data, str(typst_out_dir / "basic.pdf")))
    uris = _pdf_uris(pdf)

    assert {
        "https://github.com/moi",
        "https://moi.dev",
        "https://www.linkedin.com/in/moi",
        f"mailto:{profile.email}",
    } <= uris
    assert not any(uri.count("://") > 1 for uri in uris), uris


def test_basic_declares_the_same_editable_fields_as_compact(
    example_base, example_cv, typst_out_dir
):
    """L'écran de retouche ne connaît que des ids : basic doit exposer les MÊMES."""
    base = example_base.model_copy(
        update={
            "profile": example_base.profile.model_copy(
                update={"accroche": "Accroche de test."}
            )
        }
    )
    # Toutes les options d'affichage actives : chaque zone possible est rendue.
    options = profile_compact.model_copy(
        update={"show_accroche": True, "show_soft_skills": True}
    )
    data = build_render_input(base, example_cv, options)

    def field_ids(template: Path) -> set[str]:
        work = prepare_work_dir(template, data, typst_out_dir / template.stem)
        return {box.id for box in query_field_boxes(work)}

    basic_ids = field_ids(BASIC_TEMPLATE)
    assert basic_ids == field_ids(COMPACT_TEMPLATE)
    assert {"accroche", "hard_skills", "soft_skills", "interests"} <= basic_ids


# --- couleur (option visuelle) ------------------------------------------------


def _word_boxes(pdf: Path) -> list[tuple[str, ...]]:
    """(xMin, yMin, xMax, yMax, mot) de chaque mot du PDF, mesurés par pdftotext."""
    xml = subprocess.run(
        ["pdftotext", "-bbox", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">'
        r"([^<]*)</word>",
        xml,
    )


def test_basic_color_is_visual_only(
    example_base, example_cv, typst_out_dir, monkeypatch
):
    """Bleu par défaut, noir et blanc sur option ; la couleur ne déplace AUCUN mot.

    C'est ce qui autorise la couleur à ne pas toucher au budget du template : la place
    occupée par chaque mot est identique, seule la teinte change.
    """
    # Date de création figée : deux rendus identiques donnent les mêmes octets.
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    data = build_render_input(example_base, example_cv, profile_compact)

    def render(name: str, options: dict | None) -> Path:
        payload = dict(data)
        if options is not None:
            payload["options"] = options
        out = typst_out_dir / f"{name}.pdf"
        return Path(render_typst(BASIC_TEMPLATE, payload, str(out)))

    sans_option = render("sans-option", None)
    bleu = render("bleu", {"couleur": "bleu"})
    noir_blanc = render("noir-blanc", {"couleur": "noir-blanc"})

    assert bleu.read_bytes() == sans_option.read_bytes(), "défaut = bleu"
    assert bleu.read_bytes() != noir_blanc.read_bytes(), "la couleur change le rendu"
    assert _word_boxes(bleu) == _word_boxes(noir_blanc), "mise en page inchangée"
