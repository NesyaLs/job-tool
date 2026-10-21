"""Profils de contraintes par template.

Les règles de budget/limites ne sont pas des constantes globales : elles dépendent
du TEMPLATE (un futur template aura d'autres règles). Un TemplateProfile regroupe
TOUTES les règles ; check_budget (cv/budget.py) les applique génériquement.

Pour ajouter un template : créer son TemplateProfile, l'enregistrer dans PROFILES, et
déclarer sa TemplateSpec (libellé + options) dans TEMPLATE_SPECS.

OPTIONS D'UN TEMPLATE. Un template peut déclarer des options réglables par l'utilisateur
(TemplateOption). Deux natures, qui ne se traitent pas pareil :
  - option de MISE EN PAGE (`affects_budget=True`, `profile_field` renseigné) : elle
    change la place disponible (accroche, section soft skills). Elle est appliquée au
    TemplateProfile -> le PROFIL EFFECTIF que reçoivent fill.py, budget.py, la validation
    de l'accroche et les prompts. C'est ce qui garantit que la page est mesurée telle
    qu'elle sera rendue ;
  - option VISUELLE (`affects_budget=False`) : elle ne déplace rien (couleur). Elle ne
    touche pas au profil ; elle est seulement transmise au template (render_input).

RÉFÉRENCE DE TEMPLATE. Un choix « template + options » s'écrit en une chaîne canonique,
`nom?cle=valeur&…` (clés triées, valeurs par défaut OMISES) : « basic » seul = options par
défaut. C'est cette chaîne que stockent les brouillons (colonne `template`) et qui sert de
clé de reprise. Les anciens noms `compact-soft`, `compact-ctx` et `compact-soft-ctx`
restent valides : ce sont des ALIAS de « compact + options ».
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NamedTuple

from pydantic import BaseModel


class TemplateProfile(BaseModel):
    """Toutes les contraintes d'un template (comptages + bullets + caractères)."""

    # Entrées & projets
    min_projects: int  # nb min de PROJETS affichés (stages épinglés non comptés)
    max_projects: int  # nb max de PROJETS affichés (stages épinglés non comptés)
    max_bullets_per_entry: int  # plafond de bullets pour UNE entrée (stage ou projet)
    min_bullets_per_entry: int  # une entrée affichée a au moins ce nb de bullets

    # Compétences (sélections dans les listes maîtres)
    max_hard_skills: int
    max_soft_skills: int
    max_interests: int

    # Limites de caractères
    max_bullet_chars: int  # longueur max d'une bullet
    max_title_chars: int  # longueur max d'un titre d'entrée (expérience/projet)
    max_skills_line_chars: (
        int  # longueur max de la ligne hard skills (troncage au rendu)
    )
    # Longueur max de la ligne SOFT skills (troncage au rendu, analogue à hard). Défaut
    # pour ne pas casser les profils existants ; pertinent surtout si show_soft_skills.
    max_soft_skills_line_chars: int = 100

    # Options d'affichage du template (booléens lus au rendu). Baseline = tout False :
    # ni section soft skills, ni phrase d'accroche (comportement historique).
    show_soft_skills: bool = (
        False  # afficher une section soft skills (données de l'appel B)
    )
    show_accroche: bool = False  # afficher la phrase d'accroche (cf. Profile.accroche*)

    # Budget TOTAL de bullets indexé par le NOMBRE TOTAL D'ENTRÉES affichées
    # (stages retenus + projets retenus). Table EN DUR, lisible. Principe :
    # « moins d'entrées = plus de bullets disponibles par CV ». Hors table
    # (ex : 0 entrée, ou > clés définies), aucune limite de total n'est appliquée
    # (les autres règles, dont bornes par-entrée et caractères, s'appliquent toujours).
    bullet_budget: dict[int, int]


# Profil concret du template "compact" — BASELINE (ni soft skills ni accroche).
profile_compact = TemplateProfile(
    min_projects=2,
    max_projects=4,
    max_bullets_per_entry=5,
    min_bullets_per_entry=1,
    max_hard_skills=12,
    max_soft_skills=6,
    max_interests=4,
    max_bullet_chars=120,
    max_title_chars=80,
    max_skills_line_chars=100,
    max_soft_skills_line_chars=100,
    show_soft_skills=False,
    show_accroche=False,
    bullet_budget={
        1: 30,
        2: 30,
        3: 28,
        4: 26,
        5: 24,
        6: 22,
        7: 19,
    },
)

# Les 4 PRESETS = baseline + les combinaisons des deux options d'affichage. Mêmes
# contraintes que la baseline (AUCUN nouveau plafond de bullets : le remplissage mesuré
# s'adapte seul à la place laissée par l'accroche/les soft skills). Seuls les booléens
# d'affichage changent. Tous rendus par le MÊME template (cv_compact.typ, 2 blocs
# conditionnels) — cf. cv/render.py.
profile_compact_soft = profile_compact.model_copy(update={"show_soft_skills": True})
profile_compact_ctx = profile_compact.model_copy(update={"show_accroche": True})
profile_compact_soft_ctx = profile_compact.model_copy(
    update={"show_soft_skills": True, "show_accroche": True}
)

# Profil du template "basic" (@preview/basic-resume, cv_basic.typ). Valeurs PROVISOIRES,
# reprises de la baseline compact en attendant la calibration mesurée du template ; seule
# l'accroche est activée (choix du template, cf. docs de calibration à venir).
profile_basic = profile_compact.model_copy(update={"show_accroche": True})

# Profil du template "modern_cv" (@preview/modern-cv, cv_modern_cv.typ). Une seule colonne,
# en-tête haut (nom 32 pt, adresse et contacts sur leurs propres lignes) : c'est le template
# le MOINS capacitaire des quatre nouveaux, d'où des plafonds plus bas que compact.
# CALIBRÉ ICI avec scripts/calibrate_template.py (cf. docs/templates/modern_cv.md) ; les
# valeurs mesurées hors du projet ne valaient que comme point de départ.
profile_modern_cv = profile_compact.model_copy(
    update={
        "min_projects": 2,
        "max_projects": 3,
        # Mesuré : relever ce plafond DÉGRADE le cas nominal (5 entrées : 16 puces à 4,
        # 13 à 5, 15 à 6). Le remplissage glouton empile alors davantage sur les premières
        # entrées, sature la page plus tôt, et la fenêtre de sondage ne rattrape pas.
        "max_bullets_per_entry": 4,
        "show_accroche": True,
        "show_soft_skills": True,
        # Mesuré ici (3 passes identiques). À 4 entrées c'est max_bullets_per_entry qui
        # borne (4 × 4), pas la page.
        "bullet_budget": {4: 16, 5: 16, 6: 13},
    }
)

# Profil du template "cobalt" (recopie MIT du modèle @preview/cobalt-cv, cv_cobalt.typ).
# En-tête compact puis deux colonnes : la barre latérale porte formation et compétences, ce
# qui libère toute la colonne principale pour les expériences -> la meilleure capacité des
# quatre nouveaux. CALIBRÉ ICI (cf. docs/templates/cobalt.md).
profile_cobalt = profile_compact.model_copy(
    update={
        "min_projects": 2,
        # Mesuré : à 7 entrées (3 stages + 4 projets) le PLANCHER déborde déjà, même à une
        # puce par entrée. Le rapport d'évaluation proposait 4 projets ; la page ne les
        # tient pas ici.
        "max_projects": 3,
        # Mesuré : 6 est meilleur ou égal à 5 sur toutes les tailles (22/20/18 contre
        # 19/20/17) ; 7 dégrade le cas nominal (19). Contrairement à modern_cv, la barre
        # latérale libère assez de colonne principale pour que le plafond serve.
        "max_bullets_per_entry": 6,
        "show_accroche": True,
        "show_soft_skills": True,
        # Mesuré ici. Très loin des {4: 34, 5: 31, 6: 28} du rapport, qui donnait ses
        # chiffres pour des puces COURTES (55-65 caractères) ; la base réelle en a de
        # longues (jusqu'à max_bullet_chars = 120).
        "bullet_budget": {4: 22, 5: 20, 6: 18},
    }
)

# Profil du template "lavandula" (@preview/lavandula, cv_lavandula.typ). Barre latérale
# colorée pleine hauteur (nom, contacts, accroche, compétences en pastilles, langues,
# intérêts) ; la colonne principale ne porte que expériences, projets et formation. Puces
# à 8 pt : la capacité vient de là. CALIBRÉ ICI (cf. docs/templates/lavandula.md).
profile_lavandula = profile_compact.model_copy(
    update={
        "min_projects": 2,
        # Mesuré : 7 entrées tiennent ici (16 puces), contrairement à cobalt et metronic.
        "max_projects": 4,
        # Mesuré : 6 est le meilleur au cas nominal (21 puces à 5 entrées, contre 20 à 5
        # comme à 7). 7 gagne sur les tailles extrêmes mais perd là où le template servira.
        "max_bullets_per_entry": 6,
        "show_accroche": True,
        "show_soft_skills": True,
        # Mesuré ici. Le rapport annonçait {4: 32, 5: 28, 6: 25, 7: 22} : ses chiffres de
        # puces COURTES, sans rapport avec les puces réelles (jusqu'à 120 caractères).
        "bullet_budget": {4: 22, 5: 21, 6: 18, 7: 16},
    }
)

# Profil du template "metronic" (@preview/metronic, cv_metronic.typ). Barre latérale pleine
# hauteur en couleur d'accent ; la FORMATION est dans la colonne principale (l'exemple du
# package la met dans la barre, qui déborde alors à elle seule). Densité serrée imposée
# dans le .typ : à la taille native de 12 pt, la page déborde dès 3 expériences + 3 projets.
# CALIBRÉ ICI (cf. docs/templates/metronic.md).
profile_metronic = profile_compact.model_copy(
    update={
        "min_projects": 2,
        # Mesuré : 7 entrées tiennent (12 puces), le plancher ne déborde pas.
        "max_projects": 4,
        # Mesuré : 4 est le meilleur au cas nominal (15 puces à 5 entrées, contre 14 à 3
        # comme à 5). Le rapport proposait 3.
        "max_bullets_per_entry": 4,
        "show_accroche": True,
        "show_soft_skills": True,
        # Mesuré ici, densité serrée. C'est le template le MOINS capacitaire : le rapport
        # annonçait {4: 28, 5: 24, 6: 21} en puces courtes, sans rapport avec les puces
        # réelles. À sa taille native de 12 pt, la page déborderait purement et simplement.
        "bullet_budget": {4: 16, 5: 15, 6: 12, 7: 12},
    }
)

# get_profile(template) -> contraintes + options d'affichage du template.
#   - "compact"/"classic" : BASELINE historique (inchangée).
#   - "compact-soft" / "compact-ctx" / "compact-soft-ctx" : les 3 variantes opt-in.
PROFILES: dict[str, TemplateProfile] = {
    "compact": profile_compact,  # baseline (False, False)
    "classic": profile_compact,  # baseline, layout classic (inchangé)
    "compact-soft": profile_compact_soft,  # (True,  False)
    "compact-ctx": profile_compact_ctx,  # (False, True)
    "compact-soft-ctx": profile_compact_soft_ctx,  # (True,  True)
    "basic": profile_basic,  # layout basic-resume, accroche affichée
    "modern_cv": profile_modern_cv,  # layout modern-cv, accroche + savoir-être affichés
    "cobalt": profile_cobalt,  # layout à barre latérale, accroche + savoir-être affichés
    "lavandula": profile_lavandula,  # barre colorée, accroche + savoir-être dans la barre
    "metronic": profile_metronic,  # barre pleine hauteur, densité serrée imposée
}

DEFAULT_PROFILE = profile_compact


# --- Options de template ------------------------------------------------------

#: Valeur d'une option : booléen (case à cocher) ou valeur d'une liste de choix.
OptionValue = bool | str


class TemplateOptionError(ValueError):
    """Template ou option invalide (nom inconnu, valeur hors liste, mauvais type)."""


class OptionChoice(BaseModel):
    """Une valeur possible d'une option à choix."""

    value: str
    label: str


class TemplateOption(BaseModel):
    """Une option réglable d'un template, déclarée À CÔTÉ de son profil."""

    name: str
    label: str
    kind: Literal["bool", "choice"]
    default: OptionValue
    choices: tuple[OptionChoice, ...] = ()
    # True : l'option change la place disponible -> appliquée au profil effectif.
    affects_budget: bool
    # Champ de TemplateProfile piloté par une option de mise en page (booléenne).
    profile_field: str | None = None
    # False : cette option visuelle ne fait PAS varier la miniature. Les miniatures sont
    # produites depuis la base FICTIVE ; une option dont l'effet dépend d'une donnée que
    # cette base n'a pas (la photo de profil) donnerait des images identiques en plusieurs
    # exemplaires. Sans ce drapeau, déclarer `photo` et `taille_photo` multiplierait par
    # six le nombre d'images de metronic, toutes semblables.
    varies_preview: bool = True

    def check(self, value: object) -> OptionValue:
        """Valeur validée, ou TemplateOptionError (jamais de conversion silencieuse)."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise TemplateOptionError(
                    f"option {self.name!r} : booléen attendu, reçu {value!r}"
                )
            return value
        allowed = [choice.value for choice in self.choices]
        if value not in allowed:
            raise TemplateOptionError(
                f"option {self.name!r} : valeur {value!r} invalide "
                f"(attendu : {', '.join(allowed)})"
            )
        return str(value)


class TemplateSpec(BaseModel):
    """Ce qu'on présente d'un template : libellé, description, options réglables."""

    name: str
    label: str
    description: str
    options: tuple[TemplateOption, ...] = ()
    # Template récent, dont la calibration et le rendu n'ont pas encore été éprouvés sur
    # de vraies candidatures : l'interface l'annonce par un badge discret. PROPRIÉTÉ DU
    # TEMPLATE, pas une liste dans le JS — retirer le badge, c'est passer ce booléen à
    # False ici, et nulle part ailleurs.
    beta: bool = False


_ACCROCHE_LABEL = "Phrase d'accroche en tête du CV"

TEMPLATE_SPECS: dict[str, TemplateSpec] = {
    "classic": TemplateSpec(
        name="classic",
        label="Classic (historique)",
        description="Ancienne mise en page, conservée pour les documents existants.",
    ),
    "compact": TemplateSpec(
        name="compact",
        label="Classique",
        description="Sobre et dense : une colonne, filets fins, un maximum de contenu.",
        options=(
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=False,
                affects_budget=True,
                profile_field="show_accroche",
            ),
            TemplateOption(
                name="soft_skills",
                label="Section savoir-être",
                kind="bool",
                default=False,
                affects_budget=True,
                profile_field="show_soft_skills",
            ),
        ),
    ),
    "basic": TemplateSpec(
        name="basic",
        label="Stylé",
        description="Titres en petites capitales, nom et sections en couleur.",
        options=(
            TemplateOption(
                name="couleur",
                label="Couleur",
                kind="choice",
                default="bleu",
                choices=(
                    OptionChoice(value="bleu", label="Bleu"),
                    OptionChoice(value="noir-blanc", label="Noir et blanc"),
                ),
                affects_budget=False,
            ),
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_accroche",
            ),
        ),
    ),
    "modern_cv": TemplateSpec(
        name="modern_cv",
        label="Contrasté",
        description=(
            "Sans-sérif, nom en deux tons, contacts à icônes, titres de section colorés."
        ),
        beta=True,
        options=(
            TemplateOption(
                name="couleur",
                label="Couleur",
                kind="choice",
                default="bleu",
                choices=(
                    OptionChoice(value="bleu", label="Bleu"),
                    OptionChoice(value="bordeaux", label="Bordeaux"),
                    OptionChoice(value="vert", label="Vert"),
                    OptionChoice(value="noir", label="Noir"),
                ),
                affects_budget=False,
            ),
            TemplateOption(
                name="petites_capitales",
                label="Sous-titres en petites capitales",
                kind="bool",
                default=True,
                affects_budget=False,
            ),
            # Pied de page : le package réserve alors 20 mm en bas au lieu de 10 mm. Elle
            # change donc la place SANS piloter un champ du profil — `profile_field=None`,
            # et c'est fill.py qui mesure la page telle qu'elle sera rendue.
            TemplateOption(
                name="pied_de_page",
                label="Pied de page (nom, numéro de page)",
                kind="bool",
                default=False,
                affects_budget=True,
            ),
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_accroche",
            ),
            TemplateOption(
                name="soft_skills",
                label="Section savoir-être",
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_soft_skills",
            ),
        ),
    ),
    "cobalt": TemplateSpec(
        name="cobalt",
        label="Cobalt",
        description=(
            "Nom en capitales espacées, barre latérale ombrée (formation, compétences)."
        ),
        beta=True,
        options=(
            TemplateOption(
                name="couleur",
                label="Couleur",
                kind="choice",
                default="cobalt",
                choices=(
                    OptionChoice(value="cobalt", label="Cobalt"),
                    OptionChoice(value="bordeaux", label="Bordeaux"),
                    OptionChoice(value="vert", label="Vert"),
                    OptionChoice(value="gris", label="Gris"),
                ),
                affects_budget=False,
            ),
            TemplateOption(
                name="fond_barre",
                label="Fond de la barre latérale",
                kind="choice",
                default="gris-bleu",
                choices=(
                    OptionChoice(value="gris-bleu", label="Gris-bleu"),
                    OptionChoice(value="blanc", label="Blanc"),
                ),
                affects_budget=False,
            ),
            # Le template accepte aussi « etroite » (2fr/8fr), volontairement NON proposée :
            # la formation s'y coupe mot par mot et il ne reste qu'une trentaine de points
            # dans la barre. Une valeur qui existe dans le .typ n'a pas à être offerte.
            TemplateOption(
                name="barre",
                label="Largeur de la barre",
                kind="choice",
                default="normale",
                choices=(
                    OptionChoice(value="normale", label="Normale"),
                    OptionChoice(value="large", label="Large"),
                ),
                affects_budget=True,
            ),
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_accroche",
            ),
            TemplateOption(
                name="soft_skills",
                label="Section savoir-être",
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_soft_skills",
            ),
        ),
    ),
    "lavandula": TemplateSpec(
        name="lavandula",
        label="Lavande",
        description=(
            "Barre latérale colorée pleine hauteur, compétences en pastilles, "
            "puces à icône."
        ),
        beta=True,
        options=(
            TemplateOption(
                name="couleur",
                label="Couleur",
                kind="choice",
                default="lavande",
                choices=(
                    OptionChoice(value="lavande", label="Lavande"),
                    OptionChoice(value="sauge", label="Sauge"),
                    OptionChoice(value="ardoise", label="Ardoise"),
                ),
                affects_budget=False,
            ),
            TemplateOption(
                name="position_barre",
                label="Côté de la barre",
                kind="choice",
                default="left",
                choices=(
                    OptionChoice(value="left", label="Gauche"),
                    OptionChoice(value="right", label="Droite"),
                ),
                affects_budget=False,
            ),
            # Le template accepte aussi « etroite » (30 %), volontairement NON proposée :
            # il ne reste qu'une cinquantaine de points dans la barre, qui déborderait
            # avec une liste de compétences un peu fournie.
            TemplateOption(
                name="largeur_barre",
                label="Largeur de la barre",
                kind="choice",
                default="normale",
                choices=(
                    OptionChoice(value="normale", label="Normale"),
                    OptionChoice(value="large", label="Large"),
                ),
                affects_budget=True,
            ),
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_accroche",
            ),
            TemplateOption(
                name="soft_skills",
                label="Section savoir-être",
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_soft_skills",
            ),
        ),
    ),
    "metronic": TemplateSpec(
        name="metronic",
        label="Metronic",
        description=(
            "Barre latérale pleine hauteur en couleur, pastilles, titres à icône."
        ),
        beta=True,
        options=(
            TemplateOption(
                name="couleur",
                label="Couleur",
                kind="choice",
                default="turquoise",
                choices=(
                    OptionChoice(value="turquoise", label="Turquoise"),
                    OptionChoice(value="marine", label="Marine"),
                    OptionChoice(value="corail", label="Corail"),
                ),
                affects_budget=False,
            ),
            # La « densité » du .typ n'est PAS exposée : sa valeur native (12 pt) fait
            # déborder la page dès 3 expériences + 3 projets. Le template force « serree ».
            #
            # Photo : VISUELLE. Mesuré — elle ne retire aucune puce à la colonne
            # principale, et le template réduit ou omet le disque si la barre latérale
            # manque de place (avertissement <jtwarning> à l'appui). `photo` est lue par
            # render.py, pas par le .typ : elle décide si l'on transmet l'image.
            TemplateOption(
                name="photo",
                label="Photo de profil",
                kind="bool",
                default=True,
                affects_budget=False,
                varies_preview=False,
            ),
            TemplateOption(
                name="taille_photo",
                label="Taille de la photo",
                kind="choice",
                default="moyenne",
                choices=(
                    OptionChoice(value="petite", label="Petite"),
                    OptionChoice(value="moyenne", label="Moyenne"),
                    OptionChoice(value="grande", label="Grande"),
                ),
                affects_budget=False,
                varies_preview=False,
            ),
            TemplateOption(
                name="accroche",
                label=_ACCROCHE_LABEL,
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_accroche",
            ),
            TemplateOption(
                name="soft_skills",
                label="Section savoir-être",
                kind="bool",
                default=True,
                affects_budget=True,
                profile_field="show_soft_skills",
            ),
        ),
    ),
}

#: Anciens noms « template + options » figés dans un nom, toujours acceptés.
LEGACY_ALIASES: dict[str, tuple[str, dict[str, OptionValue]]] = {
    "compact-soft": ("compact", {"soft_skills": True}),
    "compact-ctx": ("compact", {"accroche": True}),
    "compact-soft-ctx": ("compact", {"soft_skills": True, "accroche": True}),
}


class TemplateSelection(NamedTuple):
    """Un template et TOUTES ses options résolues (défauts compris)."""

    name: str
    options: dict[str, OptionValue]


def select_template(
    name: str, options: Mapping[str, object] | None = None
) -> TemplateSelection:
    """Valide un nom (ou alias) + des options et complète les défauts.

    Un alias apporte ses options ; celles passées explicitement les remplacent. Lève
    TemplateOptionError sur un template, une option ou une valeur inconnus.
    """
    given: dict[str, object] = dict(options or {})
    if name in LEGACY_ALIASES:
        name, alias_options = LEGACY_ALIASES[name]
        given = {**alias_options, **given}
    spec = TEMPLATE_SPECS.get(name)
    if spec is None:
        raise TemplateOptionError(f"template inconnu : {name!r}")
    declared = {option.name: option for option in spec.options}
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise TemplateOptionError(
            f"option(s) inconnue(s) pour {name!r} : {', '.join(unknown)}"
        )
    resolved = {
        option.name: option.check(given[option.name])
        if option.name in given
        else option.default
        for option in spec.options
    }
    return TemplateSelection(name, resolved)


def _encode(value: OptionValue) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else value


def format_template_ref(selection: TemplateSelection) -> str:
    """Référence canonique : `nom?cle=valeur&…`, clés triées, défauts omis."""
    spec = TEMPLATE_SPECS[selection.name]
    defaults = {option.name: option.default for option in spec.options}
    changed = sorted(
        (key, value)
        for key, value in selection.options.items()
        if value != defaults[key]
    )
    if not changed:
        return selection.name
    return selection.name + "?" + "&".join(f"{k}={_encode(v)}" for k, v in changed)


def parse_template_ref(ref: str) -> TemplateSelection:
    """Inverse de format_template_ref ; accepte aussi un nom seul ou un alias."""
    name, _, query = ref.partition("?")
    raw: dict[str, object] = {}
    if query:
        for pair in query.split("&"):
            key, sep, text = pair.partition("=")
            if not sep:
                raise TemplateOptionError(f"option mal formée dans {ref!r} : {pair!r}")
            raw[key] = {"true": True, "false": False}.get(text, text)
    spec = TEMPLATE_SPECS.get(LEGACY_ALIASES.get(name, (name, {}))[0])
    if spec is not None:
        # Une option booléenne ne s'écrit que true/false ; une option à choix garde son
        # texte, même s'il ressemble à un booléen.
        for option in spec.options:
            if option.kind == "choice" and isinstance(raw.get(option.name), bool):
                raw[option.name] = _encode(raw[option.name])  # type: ignore[arg-type]
    return select_template(name, raw)


def equivalent_refs(selection: TemplateSelection) -> tuple[str, ...]:
    """Toutes les écritures stockées qui désignent CE choix (canonique + alias)."""
    refs = [format_template_ref(selection)]
    for alias in LEGACY_ALIASES:
        if parse_template_ref(alias) == selection:
            refs.append(alias)
    return tuple(refs)


def effective_profile(selection: TemplateSelection) -> TemplateProfile:
    """Profil de base du template + options de MISE EN PAGE appliquées.

    Les options visuelles n'y figurent pas : elles ne changent pas la place disponible.
    """
    updates = {
        option.profile_field: selection.options[option.name]
        for option in TEMPLATE_SPECS[selection.name].options
        if option.affects_budget and option.profile_field
    }
    base = PROFILES[selection.name]
    return base.model_copy(update=updates) if updates else base


def get_profile(template: str) -> TemplateProfile:
    """Profil EFFECTIF d'un nom, alias ou référence `nom?options` (défaut si inconnu).

    Le repli silencieux sur DEFAULT_PROFILE est conservé pour les appelants historiques ;
    la validation stricte d'un choix passe par `select_template` / `parse_template_ref`.
    """
    try:
        return effective_profile(parse_template_ref(template))
    except TemplateOptionError:
        return PROFILES.get(template, DEFAULT_PROFILE)
