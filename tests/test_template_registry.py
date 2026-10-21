"""Cohérence du catalogue des templates de CV.

Un template est déclaré à DEUX endroits : son fichier (cv/render._TEMPLATES) et son budget
(cv/profiles.PROFILES). Rien ne les relie à l'exécution, et un nom absent de PROFILES ne
lève pas : get_profile retombe en silence sur le profil compact. Ce test est le garde-fou.
"""

from __future__ import annotations

from jobtool.documents.cv.profiles import LEGACY_ALIASES, PROFILES, TEMPLATE_SPECS
from jobtool.documents.cv.render import TEMPLATES_DIR, _TEMPLATES
from jobtool.services.template_service import (
    DEFAULT_CV_TEMPLATE,
    OFFERED_CV_TEMPLATES,
    known_cv_templates,
)


def test_template_files_and_profiles_declare_the_same_names():
    assert set(_TEMPLATES) == set(PROFILES)


def test_every_declared_template_file_exists():
    for name, filename in _TEMPLATES.items():
        assert (TEMPLATES_DIR / filename).is_file(), f"{name} -> {filename} absent"


def test_offered_templates_are_known():
    assert set(OFFERED_CV_TEMPLATES) <= set(known_cv_templates())
    assert DEFAULT_CV_TEMPLATE in OFFERED_CV_TEMPLATES
    assert set(OFFERED_CV_TEMPLATES) == {
        "compact",
        "basic",
        "modern_cv",
        "cobalt",
        "lavandula",
        "metronic",
    }
    # `classic` reste rendu pour les documents existants, mais n'est plus proposé.
    assert "classic" not in OFFERED_CV_TEMPLATES


def test_every_known_name_is_a_declared_template_or_an_alias():
    """Chaque nom connu a une spécification (libellé, options) ou est un ancien alias."""
    assert set(TEMPLATE_SPECS) | set(LEGACY_ALIASES) == set(PROFILES)
    assert not set(TEMPLATE_SPECS) & set(LEGACY_ALIASES)
