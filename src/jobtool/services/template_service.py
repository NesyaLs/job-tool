"""Catalogue des templates de CV et de lettre : ce qui est CONNU, PROPOSÉ, et les options.

La lettre n'a ni options ni alias de nom : son catalogue est le registre
documents/cover_letter/registry.py, tous ses templates sont proposés. Voir la fin du module.

Deux listes, volontairement distinctes :
  - CONNUS   : tout nom accepté par la génération (cv/profiles.PROFILES : templates de base
               ET anciens alias `compact-soft`…). La CLI les expose tous.
  - PROPOSÉS : les mises en page présentées au choix dans l'interface, avec leurs options
               (déclarées dans cv/profiles.TEMPLATE_SPECS). `classic` n'y figure plus : il
               reste rendu pour les documents existants et accepté par la CLI.

Un choix « template + options » circule sous forme de RÉFÉRENCE canonique (`nom?cle=val`,
cf. cv/profiles.format_template_ref). Valider et canoniser ICI, avant d'appeler la
génération, est ce qui empêche un nom ou une option invalide de coûter deux appels au
modèle avant d'échouer au rendu.
"""

from __future__ import annotations

from collections.abc import Mapping

from jobtool.documents.cover_letter.registry import (
    DEFAULT_LETTER_TEMPLATE,
    LETTER_TEMPLATES,
    LetterTemplateError,
    LetterTemplateSpec,
    canonical_letter_template,
)
from jobtool.documents.cv.profiles import (
    PROFILES,
    TEMPLATE_SPECS,
    OptionValue,
    TemplateOptionError,
    TemplateSelection,
    TemplateSpec,
    equivalent_refs,
    format_template_ref,
    parse_template_ref,
    select_template,
)

#: Mises en page présentées au choix, dans l'ordre d'affichage.
OFFERED_CV_TEMPLATES: tuple[str, ...] = (
    "compact",
    "basic",
    "modern_cv",
    "cobalt",
    "lavandula",
    "metronic",
)

#: Template pré-sélectionné, et utilisé quand une génération n'en précise aucun.
DEFAULT_CV_TEMPLATE = "compact"

#: Un brouillon de CV au champ `template` vide date d'avant les presets : il se rend en
#: `classic` (cf. documents/drafts/kinds.py).
LEGACY_EMPTY_TEMPLATE = "classic"


def known_cv_templates() -> list[str]:
    """Tous les noms de template de CV acceptés par la génération, triés."""
    return sorted(PROFILES)


def is_known_cv_template(name: str) -> bool:
    """Vrai si `name` désigne un template de CV enregistré (base ou alias)."""
    return name in PROFILES


def offered_cv_templates() -> list[TemplateSpec]:
    """Spécifications (libellé, description, options) des mises en page proposées."""
    return [TEMPLATE_SPECS[name] for name in OFFERED_CV_TEMPLATES]


def canonical_cv_template(template: str, options: Mapping[str, object] | None) -> str:
    """Référence canonique d'un choix ; TemplateOptionError si invalide.

    `template` est un nom de base ou un ancien alias ; `options` sont validées contre la
    déclaration du template (option inconnue, mauvais type, valeur hors liste -> erreur).
    """
    return format_template_ref(select_template(template, options))


def reusable_cv_template_refs(ref: str) -> tuple[str, ...]:
    """Valeurs du champ `template` d'un brouillon qui correspond EXACTEMENT à ce choix.

    Même template et mêmes options : forme canonique, anciens alias équivalents, et le
    champ vide pour `classic` sans option.
    """
    selection = parse_template_ref(ref)
    refs = equivalent_refs(selection)
    if selection == select_template(LEGACY_EMPTY_TEMPLATE):
        refs = (*refs, "")
    return refs


# --- Miniatures -------------------------------------------------------------------

#: Dossier des miniatures, relatif à la racine du front (Interface/) : servies en statique.
PREVIEWS_DIR = "assets/templates"


def visual_option_combinations(spec: TemplateSpec) -> list[dict[str, OptionValue]]:
    """Toutes les combinaisons des options VISUELLES d'un template ([{}] s'il n'en a pas).

    Une option de mise en page (accroche…) ne donne pas d'image distincte : la miniature
    montre l'ALLURE du template, que seules les options visuelles changent nettement. Une
    option visuelle marquée `varies_preview=False` est écartée elle aussi : son effet
    dépend d'une donnée absente de la base fictive (la photo de profil), et elle ne ferait
    que dupliquer des images identiques.
    """
    combos: list[dict[str, OptionValue]] = [{}]
    for option in spec.options:
        if option.affects_budget or not option.varies_preview:
            continue
        values: list[OptionValue] = (
            [False, True]
            if option.kind == "bool"
            else [choice.value for choice in option.choices]
        )
        combos = [{**combo, option.name: value} for combo in combos for value in values]
    return combos


def preview_filename(name: str, visual_options: Mapping[str, OptionValue]) -> str:
    """Nom de fichier d'une miniature : `basic--couleur-bleu.png`, `compact.png`.

    Seule source de ce nommage : le script qui génère les images et la route qui les
    annonce l'appellent tous deux, ils ne peuvent pas diverger.
    """
    parts = [name] + [
        f"{key}-{str(value).lower()}" for key, value in sorted(visual_options.items())
    ]
    return "--".join(parts) + ".png"


def template_previews(spec: TemplateSpec) -> list[dict[str, object]]:
    """[{options visuelles, url}] des miniatures d'un template, pour la route."""
    return [
        {
            "options": combo,
            "url": f"{PREVIEWS_DIR}/{preview_filename(spec.name, combo)}",
        }
        for combo in visual_option_combinations(spec)
    ]


def describe_cv_template(ref: str) -> TemplateSelection:
    """Template de base + options résolues d'un brouillon, tolérant aux valeurs anciennes.

    Champ vide -> `classic`. Une référence illisible (template retiré depuis) n'empêche
    pas d'afficher le document : on la renvoie telle quelle, sans option.
    """
    try:
        return parse_template_ref(ref or LEGACY_EMPTY_TEMPLATE)
    except TemplateOptionError:
        return TemplateSelection(ref, {})


# --- Lettre -------------------------------------------------------------------------


def offered_letter_templates() -> list[LetterTemplateSpec]:
    """Templates de lettre proposés au choix, dans l'ordre d'affichage (défaut en tête)."""
    return list(LETTER_TEMPLATES.values())


def letter_preview_filename(name: str) -> str:
    """Nom de fichier de la miniature d'un template de lettre : `lettre--moderne.png`.

    Préfixé : les miniatures de CV et de lettre partagent le même dossier.
    """
    return f"lettre--{name}.png"


def letter_template_previews(spec: LetterTemplateSpec) -> list[dict[str, object]]:
    """[{options, url}] de la miniature d'un template de lettre (une seule, sans option).

    Même forme que `template_previews` : le front traite CV et lettre avec le même code.
    """
    return [
        {"options": {}, "url": f"{PREVIEWS_DIR}/{letter_preview_filename(spec.name)}"}
    ]


def describe_letter_template(stored: str) -> str:
    """Nom canonique du template d'une lettre enregistrée (`""`, `classic` -> `sobre`).

    Tolérant comme `describe_cv_template` : un nom illisible est renvoyé tel quel, pour
    que l'écran affiche le document (son rendu, lui, échouera avec un message clair).
    """
    try:
        return canonical_letter_template(stored)
    except LetterTemplateError:
        return stored


__all__ = [
    "DEFAULT_CV_TEMPLATE",
    "DEFAULT_LETTER_TEMPLATE",
    "LetterTemplateError",
    "canonical_letter_template",
    "describe_letter_template",
    "letter_preview_filename",
    "letter_template_previews",
    "offered_letter_templates",
    "LEGACY_EMPTY_TEMPLATE",
    "OFFERED_CV_TEMPLATES",
    "PREVIEWS_DIR",
    "OptionValue",
    "TemplateOptionError",
    "canonical_cv_template",
    "describe_cv_template",
    "is_known_cv_template",
    "known_cv_templates",
    "offered_cv_templates",
    "preview_filename",
    "reusable_cv_template_refs",
    "template_previews",
    "visual_option_combinations",
]
