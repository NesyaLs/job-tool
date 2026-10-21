"""Miniatures des templates et base fictive qui sert à les produire.

Les miniatures sont des fichiers statiques pré-générés (scripts/generate_template_previews.py).
Rien ne les recrée à l'exécution : si un template proposé ou une option VISUELLE est ajouté
sans relancer le script, la carte de l'écran Postuler afficherait une image cassée. Ces
tests transforment cet oubli en échec.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jobtool.api.main import app
from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.models import PROJECT_TYPES
from jobtool.services.template_service import (
    PREVIEWS_DIR,
    letter_template_previews,
    offered_cv_templates,
    offered_letter_templates,
    preview_filename,
    template_previews,
    visual_option_combinations,
)

FRONT_DIR = Path("Interface")
FIXTURE_BASE = Path("tests/fixtures/calibration/base.yaml")


def test_every_offered_preview_exists():
    for spec in offered_cv_templates():
        for preview in template_previews(spec):
            path = FRONT_DIR / preview["url"]
            assert path.is_file(), (
                f"miniature absente : {path} — relancer "
                "scripts/generate_template_previews.py"
            )
            assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_every_letter_preview_exists():
    for spec in offered_letter_templates():
        for preview in letter_template_previews(spec):
            path = FRONT_DIR / preview["url"]
            assert path.is_file(), (
                f"miniature absente : {path} — relancer "
                "scripts/generate_template_previews.py"
            )
            assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_calibration_fixture_has_at_most_four_contacts():
    """L'en-tête de la lettre « moderne » n'affiche proprement que 4 coordonnées."""
    profile = load_base(FIXTURE_BASE).profile
    contacts = [profile.email, profile.phone, *(profile.links or [])]
    assert len([c for c in contacts if c]) <= 4


def test_previews_follow_visual_options_only():
    specs = {spec.name: spec for spec in offered_cv_templates()}
    # compact n'a que des options de mise en page : une seule image.
    assert visual_option_combinations(specs["compact"]) == [{}]
    # basic : une image par couleur ; l'accroche (mise en page) n'en crée pas.
    assert visual_option_combinations(specs["basic"]) == [
        {"couleur": "bleu"},
        {"couleur": "noir-blanc"},
    ]
    assert [p["url"] for p in template_previews(specs["basic"])] == [
        f"{PREVIEWS_DIR}/basic--couleur-bleu.png",
        f"{PREVIEWS_DIR}/basic--couleur-noir-blanc.png",
    ]


def test_preview_filename_is_stable():
    assert preview_filename("compact", {}) == "compact.png"
    assert preview_filename("x", {"b": True, "a": "v"}) == "x--a-v--b-true.png"


def test_route_announces_the_previews():
    body = TestClient(app).get("/documents/templates").json()
    for template in body["templates"]:
        assert template["previews"], template["name"]
        for preview in template["previews"]:
            assert (FRONT_DIR / preview["url"]).is_file()


def test_calibration_fixture_is_complete_and_oversized():
    """Base fictive : tout est présent, et chaque puce respecte son template de longueur."""
    base = load_base(FIXTURE_BASE)
    assert base.profile.accroche and base.hard_skills and base.languages
    assert base.education and base.interests
    pro = [e for e in base.experiences if e.type.value not in PROJECT_TYPES]
    projects = [e for e in base.experiences if e.type.value in PROJECT_TYPES]
    assert (len(pro), len(projects)) == (3, 4)

    for exp in base.experiences:
        lengths = [len(a.text) for a in exp.achievements]
        assert all(55 <= n <= 65 or 112 <= n <= 120 for n in lengths), (exp.id, lengths)
        assert any(n <= 65 for n in lengths) and any(n >= 112 for n in lengths), exp.id


def test_an_option_whose_effect_needs_absent_data_has_no_preview():
    """`photo` / `taille_photo` ne multiplient PAS les miniatures.

    Les miniatures sont produites depuis la base FICTIVE, qui n'a pas de photo : sans
    `varies_preview=False`, metronic aurait 18 images (3 couleurs × 2 × 3) identiques six
    par six. Le test verrouille la règle, pas le nombre — il tomberait si l'on déclarait
    une nouvelle option visuelle dont l'effet dépend d'une donnée absente.
    """
    from jobtool.documents.cv.profiles import TEMPLATE_SPECS

    spec = TEMPLATE_SPECS["metronic"]
    sans_apercu = {o.name for o in spec.options if not o.varies_preview}
    assert sans_apercu == {"photo", "taille_photo"}
    combos = visual_option_combinations(spec)
    assert len(combos) == 3, "une miniature par couleur, et rien de plus"
    assert all(set(c) == {"couleur"} for c in combos)
