"""Génère les miniatures des templates de CV et de lettre proposés à l'écran Postuler.

Usage (depuis la racine du dépôt, paquet installé) :
    .venv/bin/python scripts/generate_template_previews.py

Pour chaque template proposé (services/template_service.OFFERED_CV_TEMPLATES) et chaque
combinaison de ses options VISUELLES (ex. basic en bleu / en noir et blanc) :
  1. part de la base fictive tests/fixtures/calibration/base.yaml, sans LLM : le
     « réservoir » est la base entière, dans son ordre ;
  2. passe par la VRAIE boucle de remplissage (cv/fill.py), options de mise en page à
     leur valeur par défaut — la miniature montre donc une page réellement remplie ;
  3. exporte la page en PNG avec Typst lui-même (même rendu que l'aperçu de l'écran
     Document ; un rasteriseur PDF tiers rend mal certaines polices) dans
     Interface/assets/templates/.

Pour chaque template de lettre (services/template_service.offered_letter_templates) : même
base fictive, en-tête assemblé par le vrai code (cover_letter/assemble.py) et paragraphes
fictifs écrits ci-dessous, sans LLM, date figée.

Les images sont des fichiers statiques versionnés : on relance ce script quand un
template, ses options visuelles ou la base fictive changent (un test vérifie qu'aucune
miniature attendue ne manque). Les rendus sont faits en séquence : pendant le remplissage,
render_input.json est écrit à côté du template, deux rendus simultanés s'écraseraient.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import date
from pathlib import Path

from jobtool.core.experience_base.loader import load_base
from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.rendering.typst_renderer import RenderError, render_with_fields
from jobtool.documents.cover_letter.assemble import build_cover_letter_render
from jobtool.documents.cover_letter.registry import letter_template_path
from jobtool.documents.cover_letter.render import build_render_input as letter_input
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)
from jobtool.documents.cv.fill import fill_to_one_page
from jobtool.documents.cv.profiles import get_profile, parse_template_ref
from jobtool.documents.cv.render import build_render_input, resolve_template
from jobtool.documents.cv.schema import TailoredBullet, TailoredCV, TailoredExperience
from jobtool.services.template_service import (
    PREVIEWS_DIR,
    canonical_cv_template,
    letter_preview_filename,
    offered_cv_templates,
    offered_letter_templates,
    preview_filename,
    visual_option_combinations,
)

FIXTURE_BASE = Path("tests/fixtures/calibration/base.yaml")
FRONT_DIR = Path("Interface")
WORK_DIR = Path("output/_template_previews")  # le typst empaqueté n'écrit pas dans /tmp
PREVIEW_PPI = 100  # A4 -> ~827 x 1169 px : net en grand, léger en carte
PREVIEW_DATE = date(2026, 9, 17)  # figée : relancer le script ne change pas l'image

# Lettre FICTIVE, en mode ciblé (les deux inserts présents), de longueur ordinaire.
LETTER_ROUTING = CoverLetterRoutingOutput(
    entreprise="Atelier Numérique de l'Ouest",
    domaine="logiciels de gestion pour les collectivités",
    mode="cible",
    projets_ids=["projet-fictif.1"],
    interets_ids=["Randonnée"],
)
LETTER_DRAFT = CoverLetterDraftOutput(
    paragraphe_vous=(
        "Votre atelier conçoit des logiciels de gestion utilisés chaque jour par des "
        "collectivités, avec une attention particulière portée à la fiabilité des données "
        "et à la simplicité des outils pour les agents."
    ),
    insert_interet=(
        "Cette exigence de clarté rejoint ce que je recherche dans un premier poste."
    ),
    paragraphe_moi=(
        "En dernière année d'école d'ingénieur, j'ai développé des services backend en "
        "Python et conçu des schémas de bases de données lors de deux stages, en "
        "travaillant avec des équipes produit sur des besoins concrets."
    ),
    insert_projet=(
        "J'ai notamment réalisé une API de suivi de réservations, testée et documentée, "
        "déployée pour une association locale."
    ),
    paragraphe_nous=(
        "Rejoindre votre équipe me permettrait de mettre ces acquis au service de vos "
        "produits, tout en continuant à progresser à vos côtés."
    ),
    formule=(
        "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations "
        "distinguées."
    ),
)


def full_reservoir(base: ExperienceBase) -> TailoredCV:
    """Toute la base, dans son ordre, comme si le modèle l'avait classée ainsi."""
    return TailoredCV(
        experiences=[
            TailoredExperience(
                source_experience_id=exp.id,
                bullets=[
                    TailoredBullet(source_id=ach.id, text=ach.text)
                    for ach in exp.achievements
                ],
            )
            for exp in base.experiences
        ],
        hard_skills=list(base.hard_skills),
        soft_skills=list(base.soft_skills),
        interests=list(base.interests),
    )


def render_preview(template: Path, data: dict, filename: str, out_dir: Path) -> None:
    """Rend une page en PNG dans le dossier de travail puis la copie ; lève si > 1 page."""
    artifacts = render_with_fields(
        template,
        data,
        WORK_DIR / filename.removesuffix(".png"),
        preview_ppi=PREVIEW_PPI,
    )
    if len(artifacts.previews) != 1:
        raise RenderError(f"{filename} : {len(artifacts.previews)} pages, attendu 1")
    shutil.copyfile(artifacts.previews[0], out_dir / filename)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=FRONT_DIR / PREVIEWS_DIR,
        help="dossier de sortie des PNG (défaut : Interface/assets/templates)",
    )
    args = parser.parse_args()

    base = load_base(FIXTURE_BASE)
    reservoir = full_reservoir(base)
    args.out.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    for spec in offered_cv_templates():
        for visual in visual_option_combinations(spec):
            ref = canonical_cv_template(spec.name, visual)
            profile = get_profile(ref)
            filled, report = fill_to_one_page(base, reservoir, profile, template=ref)

            filename = preview_filename(spec.name, visual)
            options = parse_template_ref(ref).options  # toutes, défauts compris
            data = build_render_input(base, filled, profile, options)
            render_preview(resolve_template(ref), data, filename, args.out)
            print(
                f"{ref:32} -> {args.out / filename} "
                f"({report.final_entries} entrées, {report.final_bullets} puces)"
            )

    letter = letter_input(
        build_cover_letter_render(base, LETTER_ROUTING, LETTER_DRAFT, PREVIEW_DATE)
    )
    for spec in offered_letter_templates():
        filename = letter_preview_filename(spec.name)
        render_preview(letter_template_path(spec.name), letter, filename, args.out)
        print(f"{'lettre ' + spec.name:32} -> {args.out / filename}")


if __name__ == "__main__":
    main()
