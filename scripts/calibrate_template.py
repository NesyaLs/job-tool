"""Calibre le profil d'un template de CV avec la VRAIE boucle de remplissage.

Usage (depuis la racine du dépôt, paquet installé) :
    .venv/bin/python scripts/calibrate_template.py                 # tous les proposés
    .venv/bin/python scripts/calibrate_template.py modern_cv       # un seul

Les budgets d'un TemplateProfile (`bullet_budget`, `max_bullets_per_entry`,
`min_projects`/`max_projects`) ne se devinent pas : ils se mesurent SUR LE PDF RENDU. Ce
script ne mesure donc rien lui-même — il fait tourner `fill.py`, qui rend et compte les
pages, et se contente de rapporter ce que la boucle a obtenu. Un budget recopié d'un
rapport produit ailleurs (autres polices, autre version de Typst) n'a aucune valeur ici.

Pour chaque template et CHAQUE combinaison de ses options de MISE EN PAGE (celles à
`affects_budget=True` : accroche, savoir-être, largeur de barre…), on part de la base
fictive `tests/fixtures/calibration/base.yaml`, réservoir = la base entière dans son
ordre, sans LLM. On relève du FillReport :

  - `floor_overflow`   : le PLANCHER déborde déjà -> le profil est infaisable, à corriger
                         avant toute autre lecture (min_projects/min_bullets trop hauts) ;
  - `final_entries`    : entrées réellement affichées -> borne haute de `bullet_budget` ;
  - `final_bullets`    : puces réellement tenues -> valeur de `bullet_budget[entrées]` ;
  - `stopped_on_overflow` : la page a bien été remplie jusqu'au débordement. FAUX signifie
                         que le remplissage s'est arrêté sur un PLAFOND du profil (ou a
                         épuisé le réservoir) : la page n'est pas pleine, et le plafond
                         peut probablement être relevé.

Les options VISUELLES (couleur…) ne sont pas parcourues : elles ne déplacent rien. Si l'une
d'elles changeait la place disponible, c'est qu'elle est mal déclarée.
"""

from __future__ import annotations

import argparse
import itertools
from pathlib import Path

from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.models import PROJECT_TYPES
from jobtool.documents.cv.fill import fill_to_one_page
from jobtool.documents.cv.profiles import (
    TEMPLATE_SPECS,
    OptionValue,
    TemplateSpec,
    effective_profile,
    select_template,
)
from jobtool.services.template_service import (
    OFFERED_CV_TEMPLATES,
    canonical_cv_template,
)

FIXTURE_BASE = Path("tests/fixtures/calibration/base.yaml")


def layout_option_combinations(spec: TemplateSpec) -> list[dict[str, OptionValue]]:
    """Combinaisons des options de MISE EN PAGE ([{}] si le template n'en a aucune).

    Symétrique de `template_service.visual_option_combinations`, qui parcourt l'autre
    moitié des options : ensemble, les deux couvrent toutes les déclarations.
    """
    layout = [option for option in spec.options if option.affects_budget]
    if not layout:
        return [{}]
    values = [
        [False, True]
        if option.kind == "bool"
        else [choice.value for choice in option.choices]
        for option in layout
    ]
    return [
        dict(zip((option.name for option in layout), combo, strict=True))
        for combo in itertools.product(*values)
    ]


def budget_table(name: str) -> dict[int, int]:
    """`bullet_budget` mesuré : {nombre d'entrées affichées -> puces tenues}.

    `bullet_budget` est indexé par le NOMBRE TOTAL D'ENTRÉES, mais une exécution n'en
    produit qu'un seul : sur la base fictive, les stages sont épinglés (plancher non
    négociable) et le remplissage s'arrête là où la page est pleine — 5 entrées, toujours.
    Pour couvrir les autres tailles de CV, on FORCE le nombre de projets en fixant
    `min_projects = max_projects = k`, ce qui donne k + (stages épinglés) entrées, et on
    relève ce que la page tient à chaque fois.

    Un `floor_overflow` signifie que ce nombre d'entrées ne tient PAS, même à une puce
    chacune : la clé est alors absente de la table, et cette absence est l'information —
    la plus grande clé de `bullet_budget` est le nombre maximal d'entrées acceptable.

    Options de mise en page aux valeurs PAR DÉFAUT du template : c'est la configuration
    dans laquelle le budget sera appliqué.
    """
    from generate_template_previews import full_reservoir

    base = load_base(FIXTURE_BASE)
    reservoir = full_reservoir(base)
    ref = canonical_cv_template(name, {})
    profile = effective_profile(select_template(name))
    projets = sum(1 for exp in base.experiences if exp.type in PROJECT_TYPES)

    mesures: dict[int, int] = {}
    for k in range(1, projets + 1):
        variante = profile.model_copy(update={"min_projects": k, "max_projects": k})
        _, report = fill_to_one_page(base, reservoir, variante, template=ref)
        if report.floor_overflow:
            continue
        mesures[report.final_entries] = max(
            mesures.get(report.final_entries, 0), report.final_bullets
        )
    return dict(sorted(mesures.items()))


def calibrate(name: str) -> None:
    """Mesure et affiche une ligne par combinaison d'options de mise en page."""
    from generate_template_previews import full_reservoir  # même réservoir fictif

    base = load_base(FIXTURE_BASE)
    reservoir = full_reservoir(base)
    spec = TEMPLATE_SPECS[name]

    print(f"\n=== {name} ({spec.label}) " + "=" * (56 - len(name) - len(spec.label)))
    print(
        f"{'options de mise en page':38} {'entrées':>7} {'puces':>6} "
        f"{'rendus':>7}  remarques"
    )
    for options in layout_option_combinations(spec):
        ref = canonical_cv_template(name, options)
        profile = effective_profile(select_template(name, options))
        _, report = fill_to_one_page(base, reservoir, profile, template=ref)

        notes = []
        if report.floor_overflow:
            notes.append("PLANCHER DÉBORDE (profil infaisable)")
        if not report.stopped_on_overflow:
            notes.append("arrêt sur un PLAFOND du profil, page non pleine")
        if report.gap_fills:
            notes.append(f"{report.gap_fills} creux comblé(s)")
        label = ", ".join(f"{k}={v}" for k, v in sorted(options.items())) or "(aucune)"
        print(
            f"{label:38} {report.final_entries:>7} {report.final_bullets:>6} "
            f"{report.renders:>7}  {'; '.join(notes)}"
        )

    mesure = budget_table(name)
    declare = dict(
        sorted(effective_profile(select_template(name)).bullet_budget.items())
    )
    print(f"\nbullet_budget MESURÉ (options par défaut) : {mesure}")
    if declare != mesure:
        print(f"bullet_budget DÉCLARÉ dans profiles.py   : {declare}")
        ecarts = {
            entries: (declare.get(entries), mesure.get(entries))
            for entries in sorted(set(declare) | set(mesure))
            if declare.get(entries) != mesure.get(entries)
        }
        print(f"  écarts {{entrées: (déclaré, mesuré)}} : {ecarts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "templates",
        nargs="*",
        default=list(OFFERED_CV_TEMPLATES),
        help="noms à calibrer (défaut : toutes les mises en page proposées)",
    )
    args = parser.parse_args()
    for name in args.templates:
        if name not in TEMPLATE_SPECS:
            raise SystemExit(f"template inconnu : {name}")
        calibrate(name)


if __name__ == "__main__":
    main()
