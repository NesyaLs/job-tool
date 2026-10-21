"""Templates de lettre : registre, CONTRAT commun et rendu réel de chaque template.

Le contrat (cf. documents/cover_letter/registry.py) n'est écrit nulle part dans le code
qui l'utilise : overrides.py suppose les ids et la concaténation des inserts, le contrôle
ATS suppose nom / entreprise / date d'un seul tenant. Un template qui s'en écarte casse
EN SILENCE. Chaque test paramétré ci-dessous tourne donc sur TOUT le registre : un template
ajouté demain y passe sans qu'on ait à y penser.

Typst réel, sans LLM. Le texte est vérifié sur l'extraction pdftotext, jamais sur les
octets (Typst embarque un horodatage).
"""

from __future__ import annotations

import pytest

from jobtool.core.rendering.pdf_checks import (
    check_pdf_ats,
    count_pdf_pages,
    extract_pdf_text,
)
from jobtool.core.rendering.typst_renderer import render_with_fields
from jobtool.documents.cover_letter import draft_checks
from jobtool.documents.cover_letter.registry import (
    DEFAULT_LETTER_TEMPLATE,
    LETTER_FIELD_IDS,
    LETTER_TEMPLATES,
    LetterTemplateError,
    canonical_letter_template,
    letter_template_path,
)
from jobtool.documents.cover_letter.render import build_render_input
from jobtool.documents.cover_letter.schema import CoverLetterRender
from jobtool.documents.cover_letter.source import normalize_letter_source
from jobtool.documents.drafts.checks import letter_ats_targets
from jobtool.documents.drafts.overrides import apply_letter

TEMPLATES = sorted(LETTER_TEMPLATES)
INTERET = "INSERT_INTERET_STUB"
PROJET = "INSERT_PROJET_STUB"


def _letter(**override) -> dict:
    """Données de rendu fictives, complètes (objet et inserts compris)."""
    fields = dict(
        nom="Lucas Moreau",
        coordonnees=[
            "lucas.moreau@example.com",
            "+33 6 12 34 56 78",
            "www.github.com/lucas-moreau",
        ],
        date="1er octobre 2026",
        lieu="Lyon",
        entreprise="L'Atelier Numérique",
        objet="Candidature au poste d'ingénieur logiciel",
        titre="Ingénieur logiciel",
        paragraphe_vous="Votre entreprise conçoit des services de paiement fiables.",
        paragraphe_moi="J'ai conçu et mis en production des API robustes et testées.",
        paragraphe_nous="Rejoindre vos équipes me permettrait de contribuer à vos projets.",
        formule="Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.",
        show_interet=True,
        insert_interet=INTERET,
        show_projet=True,
        insert_projet=PROJET,
    )
    fields.update(override)
    return build_render_input(CoverLetterRender(**fields))


def _render(tmp, template: str, data: dict, label: str = "doc"):
    return render_with_fields(letter_template_path(template), data, tmp / label)


def _flat(pdf: str) -> str:
    return " ".join(extract_pdf_text(pdf).split())


# --- Registre ------------------------------------------------------------------------


def test_registry_files_exist_and_default_is_known():
    for name, spec in LETTER_TEMPLATES.items():
        assert spec.name == name
        assert letter_template_path(name).is_file(), name
    assert DEFAULT_LETTER_TEMPLATE == "sobre"
    assert set(LETTER_TEMPLATES) == {"sobre", "moderne"}


@pytest.mark.parametrize("legacy", ["", "classic", "sobre"])
def test_legacy_names_designate_the_historical_template(legacy):
    assert canonical_letter_template(legacy) == "sobre"
    assert letter_template_path(legacy).name == "lettre.typ"


@pytest.mark.parametrize(
    "bad", ["inconnu", "lettre.typ", "../cv/cv_basic.typ", "Sobre"]
)
def test_unknown_name_is_refused_never_a_path(bad):
    with pytest.raises(LetterTemplateError):
        letter_template_path(bad)


# --- Contrat commun ------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_contract_fields_inserts_and_ats(template, typst_out_dir):
    data = _letter()
    artifacts = _render(typst_out_dir, template, data)

    texts = {box.id: box.text for box in artifacts.fields}
    assert tuple(sorted(texts)) == tuple(sorted(LETTER_FIELD_IDS))
    # Inserts concaténés avec UN espace, comme lettre.typ : overrides.py en dépend.
    assert texts["paragraphe_vous"] == data["paragraphe_vous"] + " " + INTERET
    assert texts["paragraphe_moi"] == data["paragraphe_moi"] + " " + PROJET
    # Texte BRUT enregistré (apostrophes droites) : valable d'un template à l'autre.
    assert texts["objet"] == data["objet"]
    assert texts["formule"] == data["formule"]

    assert count_pdf_pages(artifacts.pdf_path) == 1
    assert check_pdf_ats(artifacts.pdf_path, letter_ats_targets(data)) == []
    assert artifacts.warnings == []


@pytest.mark.parametrize("template", TEMPLATES)
def test_generic_case_without_inserts_or_object(template, typst_out_dir):
    data = _letter(objet=None, show_interet=False, show_projet=False)
    artifacts = _render(typst_out_dir, template, data)

    assert {box.id for box in artifacts.fields} == set(LETTER_FIELD_IDS) - {"objet"}
    flat = _flat(artifacts.pdf_path)
    assert INTERET not in flat and PROJET not in flat
    assert "Objet" not in flat
    assert count_pdf_pages(artifacts.pdf_path) == 1


@pytest.mark.parametrize("template", TEMPLATES)
def test_override_of_a_composed_paragraph_is_not_duplicated(template, typst_out_dir):
    """Une retouche porte le texte composé : l'insert ne doit pas être ajouté deux fois."""
    data = _letter()
    edited = apply_letter(dict(data), {"paragraphe_vous": "Texte retouché à la main."})
    artifacts = _render(typst_out_dir, template, edited)

    flat = _flat(artifacts.pdf_path)
    assert "Texte retouché à la main." in flat
    assert INTERET not in flat


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_optional_field_empty_leaves_no_broken_text(template, typst_out_dir):
    data = normalize_letter_source(
        _letter(
            coordonnees=[],
            lieu="",
            objet=None,
            titre="",
            destinataire="",
            salutation="",
            show_interet=False,
            show_projet=False,
        )
    )
    artifacts = _render(typst_out_dir, template, data)

    flat = _flat(artifacts.pdf_path)
    assert count_pdf_pages(artifacts.pdf_path) == 1
    assert check_pdf_ats(artifacts.pdf_path, letter_ats_targets(data)) == []
    for broken in ("Sincerely", "Objet :", "Service Recrutement"):
        assert broken not in flat, broken
    # Salutation vide : aucune ligne « Madame, Monsieur, » seule (la formule en contient).
    lines = [line.strip() for line in extract_pdf_text(artifacts.pdf_path).splitlines()]
    assert "Madame, Monsieur," not in lines
    assert artifacts.warnings == []


@pytest.mark.parametrize("template", TEMPLATES)
def test_texts_at_maximum_length_fit_on_one_page(template, typst_out_dir):
    """Plafonds de la passe 2 atteints partout, en-tête et objet longs : une page."""

    def filler(n: int) -> str:
        words = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod. "
        )
        return (words * (n // len(words) + 1))[:n].rstrip()

    data = _letter(
        entreprise="Entreprise Fictive Industrie et Services Numériques du Grand Ouest",
        objet="Candidature au poste d'ingénieur logiciel backend confirmé, équipe données",
        destinataire="Madame la Responsable du recrutement des équipes techniques",
        # Le plus long titre qui tienne sur une ligne (au-delà : avertissement).
        titre="Ingénieure logicielle backend & données (H/F)",
        coordonnees=[
            "lucas.moreau@example.com",
            "+33 6 12 34 56 78",
            "https://github.com/lucas-moreau",
            "https://www.linkedin.com/in/lucas-moreau",
        ],
        paragraphe_vous=filler(draft_checks.MAX_VOUS_CHARS),
        insert_interet=filler(draft_checks.MAX_INSERT_INTERET_CHARS),
        paragraphe_moi=filler(draft_checks.MAX_MOI_CHARS),
        insert_projet=filler(draft_checks.MAX_INSERT_PROJET_CHARS),
        paragraphe_nous=filler(draft_checks.MAX_NOUS_CHARS),
        formule=filler(draft_checks.MAX_FORMULE_CHARS),
    )
    artifacts = _render(typst_out_dir, template, data)

    assert count_pdf_pages(artifacts.pdf_path) == 1
    assert check_pdf_ats(artifacts.pdf_path, letter_ats_targets(data)) == []
    assert artifacts.warnings == []


# --- Propre au template « moderne » ---------------------------------------------------


def test_moderne_renders_the_prototype_details(typst_out_dir):
    artifacts = _render(typst_out_dir, "moderne", _letter())
    flat = _flat(artifacts.pdf_path)

    assert "Service Recrutement" in flat
    assert "Madame, Monsieur," in flat
    assert "Ingénieur logiciel" in flat  # titre sous le nom
    assert "github.com/lucas-moreau" in flat
    assert "https://" not in flat and "www." not in flat
    assert "L’Atelier Numérique" in flat  # apostrophe typographique à l'affichage
    assert "Sincerely" not in flat


def test_moderne_warns_on_long_title_and_too_many_contacts(typst_out_dir):
    data = _letter(
        titre="Ingénieur en intelligence artificielle et calcul haute performance",
        coordonnees=["a@example.com", "+33 6 12 34 56 78", "x.dev", "y.dev", "z.dev"],
    )
    artifacts = _render(typst_out_dir, "moderne", data)

    assert len(artifacts.warnings) == 2
    assert any("titre" in w for w in artifacts.warnings)
    assert any("5 coordonnées" in w for w in artifacts.warnings)
    # Avertir ne coupe rien : les cinq coordonnées sont imprimées.
    assert "z.dev" in _flat(artifacts.pdf_path)


def test_ats_ignores_the_apostrophe_form(typst_out_dir):
    """« L'Atelier » dans les données, « L’Atelier » dans le PDF : même texte pour l'ATS."""
    artifacts = _render(typst_out_dir, "moderne", _letter())
    assert "L'Atelier Numérique" not in extract_pdf_text(artifacts.pdf_path)
    assert check_pdf_ats(artifacts.pdf_path, ["L'Atelier Numérique"]) == []
    assert check_pdf_ats(artifacts.pdf_path, ["L'Atelier Absent"]) == [
        "L'Atelier Absent"
    ]


def test_draft_saved_before_the_new_fields_is_completed():
    """Un render_input d'avant titre/destinataire/salutation se recompile avec les défauts."""
    old = {
        k: v
        for k, v in _letter().items()
        if k not in ("titre", "destinataire", "salutation")
    }
    completed = normalize_letter_source(old)
    assert completed["salutation"] == "Madame, Monsieur,"
    assert completed["destinataire"] == "Service Recrutement"
    assert completed["titre"] == ""
