"""Options de template de CV : déclaration, validation, profil effectif, clé de reprise.

Sans LLM ni Typst (sauf un rendu court) : tout ce qui est vérifié ici est du code pur —
la traduction d'un choix « template + options » en profil de contraintes, en données de
rendu et en clé de brouillon.
"""

from __future__ import annotations

import pytest

from jobtool.documents.cv.profiles import (
    LEGACY_ALIASES,
    PROFILES,
    TEMPLATE_SPECS,
    TemplateOptionError,
    TemplateProfile,
    TemplateSelection,
    effective_profile,
    format_template_ref,
    get_profile,
    parse_template_ref,
    select_template,
)
from jobtool.documents.cv.render import (
    _resolve_template,
    _template_options,
    build_render_input,
)
from jobtool.services.template_service import (
    DEFAULT_CV_TEMPLATE,
    OFFERED_CV_TEMPLATES,
    canonical_cv_template,
    describe_cv_template,
    reusable_cv_template_refs,
)

# --- validation -----------------------------------------------------------------


def test_defaults_are_filled_in():
    assert select_template("basic") == TemplateSelection(
        "basic", {"couleur": "bleu", "accroche": True}
    )
    assert select_template("compact").options == {
        "accroche": False,
        "soft_skills": False,
    }
    assert select_template("classic").options == {}


@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("inconnu", {}, "template inconnu"),
        ("basic", {"police": "Arial"}, "inconnue"),
        ("basic", {"couleur": "rouge"}, "invalide"),
        ("basic", {"accroche": "true"}, "booléen attendu"),
        ("compact", {"soft_skills": 1}, "booléen attendu"),
        ("classic", {"accroche": True}, "inconnue"),
    ],
)
def test_invalid_choices_are_rejected(name, options, message):
    with pytest.raises(TemplateOptionError, match=message):
        select_template(name, options)


def test_every_option_default_is_itself_valid():
    for spec in TEMPLATE_SPECS.values():
        for option in spec.options:
            assert option.check(option.default) == option.default
            # Une option de mise en page peut ne piloter AUCUN champ du profil
            # (`pied_de_page` : elle déplace la page, mais aucun booléen ne la représente ;
            # c'est fill.py qui mesure). En revanche, un champ NOMMÉ doit exister.
            if option.profile_field is not None:
                assert option.profile_field in TemplateProfile.model_fields, (
                    f"{spec.name}.{option.name} pilote un champ inexistant"
                )
                assert option.affects_budget, (
                    f"{spec.name}.{option.name} pilote un champ du profil sans se "
                    "déclarer comme option de mise en page"
                )


def test_offered_templates_are_declared():
    assert set(OFFERED_CV_TEMPLATES) <= set(TEMPLATE_SPECS)
    assert DEFAULT_CV_TEMPLATE in OFFERED_CV_TEMPLATES
    labels = {TEMPLATE_SPECS[name].label for name in OFFERED_CV_TEMPLATES}
    assert labels == {
        "Classique",
        "Stylé",
        "Contrasté",
        "Cobalt",
        "Lavande",
        "Metronic",
    }
    # Un libellé identifie une mise en page à l'écran : deux templates n'en partagent pas.
    assert len(labels) == len(OFFERED_CV_TEMPLATES), "libellés en collision"


def test_every_offered_template_has_a_profile_and_a_typst_file():
    """Un template proposé sans profil ou sans `.typ` échouerait au premier rendu réel.

    Les trois déclarations (PROFILES, TEMPLATE_SPECS, _TEMPLATES de render.py) vivent dans
    deux modules : rien n'oblige à les tenir alignées, sauf ce test.
    """
    from jobtool.documents.cv.render import TEMPLATES_DIR, _TEMPLATES

    for name in OFFERED_CV_TEMPLATES:
        assert name in PROFILES, f"{name} n'a pas de TemplateProfile"
        assert name in _TEMPLATES, f"{name} n'est associé à aucun fichier .typ"
        assert (TEMPLATES_DIR / _TEMPLATES[name]).is_file(), (
            f"{name} : fichier {_TEMPLATES[name]} absent du paquet"
        )


def test_a_layout_option_without_profile_field_is_allowed():
    """Une option de mise en page peut ne piloter AUCUN champ du profil.

    Cas de `pied_de_page` (Contrasté) : elle change la place disponible sans correspondre
    à un booléen de TemplateProfile. `effective_profile` doit alors ne rien modifier —
    c'est fill.py qui mesure la page réelle. Le contraire (une KeyError, ou un champ
    inventé) casserait le rendu pour toute option de ce type.
    """
    option = next(
        o for o in TEMPLATE_SPECS["modern_cv"].options if o.name == "pied_de_page"
    )
    assert option.affects_budget is True and option.profile_field is None
    avec = effective_profile(select_template("modern_cv", {"pied_de_page": True}))
    sans = effective_profile(select_template("modern_cv", {"pied_de_page": False}))
    assert avec == sans, "aucune option sans profile_field ne doit toucher au profil"


# --- référence canonique ------------------------------------------------------------


def test_canonical_ref_omits_defaults_and_sorts_keys():
    assert canonical_cv_template("basic", {}) == "basic"
    assert (
        canonical_cv_template("basic", {"couleur": "bleu", "accroche": True}) == "basic"
    )
    assert (
        canonical_cv_template("basic", {"couleur": "noir-blanc", "accroche": False})
        == "basic?accroche=false&couleur=noir-blanc"
    )


@pytest.mark.parametrize(
    "selection",
    [
        select_template("basic", {"couleur": "noir-blanc"}),
        select_template("basic", {"accroche": False}),
        select_template("compact", {"soft_skills": True, "accroche": True}),
        select_template("classic"),
    ],
)
def test_ref_round_trips(selection):
    assert parse_template_ref(format_template_ref(selection)) == selection


def test_malformed_ref_is_rejected():
    with pytest.raises(TemplateOptionError):
        parse_template_ref("basic?couleur")
    with pytest.raises(TemplateOptionError):
        parse_template_ref("basic?couleur=rouge")


# --- anciens noms -----------------------------------------------------------------


@pytest.mark.parametrize("alias", sorted(LEGACY_ALIASES))
def test_legacy_names_keep_exactly_their_profile(alias):
    """compact-soft & co : même profil qu'avant, exprimé comme « compact + options »."""
    selection = parse_template_ref(alias)
    assert selection.name == "compact"
    assert effective_profile(selection) == PROFILES[alias]
    assert get_profile(alias) == PROFILES[alias]
    assert _resolve_template(alias).name == "cv_compact.typ"


def test_legacy_alias_and_its_canonical_form_share_the_reuse_key():
    refs = reusable_cv_template_refs("compact?soft_skills=true")
    assert set(refs) == {"compact?soft_skills=true", "compact-soft"}
    # L'alias explicite, avec une option en plus, n'est plus le même choix.
    assert canonical_cv_template("compact-soft", {"accroche": True}) == (
        "compact?accroche=true&soft_skills=true"
    )


# --- profil effectif ----------------------------------------------------------------


def test_accroche_option_drives_the_effective_profile():
    with_accroche = get_profile("basic")
    without = get_profile("basic?accroche=false")
    assert with_accroche.show_accroche is True
    assert without.show_accroche is False
    # Rien d'autre ne bouge : seule la place de l'accroche change.
    assert without == with_accroche.model_copy(update={"show_accroche": False})


def test_visual_option_does_not_touch_the_profile():
    assert get_profile("basic?couleur=noir-blanc") == get_profile("basic")


def test_unknown_names_still_fall_back_silently():
    """Comportement historique de get_profile conservé (la validation est en amont)."""
    assert get_profile("inconnu") == PROFILES["compact"]


# --- clé de reprise ------------------------------------------------------------------


def test_reuse_key_includes_the_options():
    assert reusable_cv_template_refs("basic") == ("basic",)
    assert "basic" not in reusable_cv_template_refs("basic?couleur=noir-blanc")


def test_empty_legacy_draft_counts_as_classic():
    assert "" in reusable_cv_template_refs("classic")
    assert "" not in reusable_cv_template_refs("compact")
    assert describe_cv_template("") == TemplateSelection("classic", {})


def test_describe_tolerates_a_removed_template():
    assert describe_cv_template("basic_eng") == TemplateSelection("basic_eng", {})


# --- jusqu'au rendu ----------------------------------------------------------------


def test_options_reach_the_render_input(example_base, example_cv):
    ref = "basic?accroche=false&couleur=noir-blanc"
    base = example_base.model_copy(
        update={
            "profile": example_base.profile.model_copy(update={"accroche": "Accroche."})
        }
    )
    data = build_render_input(
        base, example_cv, get_profile(ref), _template_options(ref)
    )

    assert data["options"] == {"couleur": "noir-blanc", "accroche": False}
    assert data["show_accroche"] is False and data["accroche"] == ""


def test_render_input_of_a_template_without_options(example_base, example_cv):
    data = build_render_input(
        example_base, example_cv, get_profile("classic"), _template_options("classic")
    )
    assert data["options"] == {}
