"""Remplissage déterministe d'un CV, mesuré sur le PDF rendu.

PRINCIPE DIRECTEUR (à ne jamais inverser) : le LLM (appel A) fournit un RÉSERVOIR
classé par pertinence (expériences ordonnées, et pour chacune ses bullets ordonnés).
Le CODE décide combien d'éléments tiennent réellement sur UNE page, en RENDANT puis
en MESURANT le PDF à chaque ajout. Le jugement (classer) appartient au modèle ; la
borne (ce qui tient) est mesurée par le code.

STRATÉGIE largeur vs profondeur : on ne force NI l'une NI l'autre. On suit l'ordre du
réservoir — expériences par pertinence décroissante, bullets par pertinence décroissante
au sein de chaque entrée (ordre (index_expérience, index_bullet)). C'est donc le
classement du modèle qui décide naturellement s'il vaut mieux approfondir une entrée
déjà ouverte ou en ouvrir une nouvelle.

ALGORITHME (glouton, une seule passe, arrêt au premier débordement) :
  1. PLANCHER non négociable : tous les stages épinglés, plus les min_projects premiers
     PROJETS du classement ; chaque entrée du plancher reçoit ses min_bullets premiers
     bullets. On rend le plancher : s'il déborde DÉJÀ sur 2 pages, on n'ajoute rien et
     on signale floor_overflow (pdf_checks remontera la violation 1-page habituelle).
  2. REMPLISSAGE glouton : on parcourt les ajouts candidats (tous les bullets non encore
     retenus) dans l'ordre du réservoir. Pour chacun : on l'ajoute, on REND, on MESURE.
       • PDF sur 1 page  -> on garde, on continue ;
       • PDF sur 2 pages -> on RETIRE ce candidat et on PASSE AU SUIVANT (on ne s'arrête
         pas au 1er débordement : un candidat plus court, derrière, peut combler le creux).
     Après le 1er débordement, l'exploration est bornée à OVERFLOW_PROBE_WINDOW candidats
     supplémentaires (fenêtre de pertinence) : on comble un petit creux avec un voisin de
     classement sans aller racler des éléments peu pertinents en fin de liste.
  3. PLAFONDS du profil respectés : jamais plus de max_projects projets, jamais plus de
     max_bullets_per_entry bullets sur une entrée.
  4. JAMAIS d'entrée affichée sans bullet : une entrée ne s'ouvre qu'en lui ajoutant un
     bullet ; si le candidat retiré était le seul bullet d'une entrée fraîchement ouverte,
     l'entrée entière disparaît avec lui.

Réutilise render_cv (rendu Typst existant) et count_pdf_pages (core/rendering/pdf_checks).
Aucun appel LLM, aucune nouvelle dépendance, aucun moteur de rendu réécrit.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.core.rendering.pdf_checks import count_pdf_pages
from jobtool.documents.cv.profiles import DEFAULT_PROFILE, TemplateProfile
from jobtool.documents.cv.render import DEFAULT_TEMPLATE, render_cv
from jobtool.documents.cv.schema import TailoredCV, TailoredExperience

# Après le PREMIER débordement, on NE s'arrête PAS : un candidat trop lourd (souvent le
# 1er bullet d'un nouveau projet = titre + dates + ligne) peut déborder alors qu'un
# candidat suivant, plus court, comblerait le creux. On saute donc le candidat débordant
# et on essaie les SUIVANTS. Mais on borne cette exploration à une petite FENÊTRE : au-delà
# de OVERFLOW_PROBE_WINDOW candidats examinés après le 1er débordement, on arrête. Sans cette
# borne, on remonterait des éléments peu pertinents de la fin du classement juste parce
# qu'ils sont petits. N petit = on comble un creux avec un VOISIN de classement, pas avec
# du remplissage faiblement pertinent.
OVERFLOW_PROBE_WINDOW = 3


@dataclass
class FillReport:
    """Observabilité du remplissage (jamais un effet de bord caché)."""

    renders: int = 0  # nb de compilations Typst effectuées
    floor_overflow: bool = False  # le plancher déborde déjà la page
    stopped_on_overflow: bool = (
        False  # au moins un candidat a débordé (page pleine atteinte)
    )
    gap_probes: int = 0  # candidats essayés APRÈS le 1er débordement (fenêtre)
    gap_fills: int = 0  # parmi eux, ceux qui ont effectivement comblé le creux
    final_entries: int = 0
    final_bullets: int = 0


def _build_cv(reservoir: TailoredCV, selected: dict[int, list[int]]) -> TailoredCV:
    """Construit un TailoredCV depuis la sélection (index_exp -> [index_bullets]).

    Les expériences sortent dans l'ordre du réservoir, leurs bullets dans l'ordre du
    réservoir. Les compétences/intérêts sont recopiés tels quels (le remplissage ne
    touche QUE les expériences ; le troncage des compétences reste géré au rendu).
    """
    experiences: list[TailoredExperience] = []
    for exp_idx in sorted(selected):
        bullet_idxs = selected[exp_idx]
        if not bullet_idxs:
            continue  # garde-fou anti-entrée-vide (ne devrait pas arriver)
        source = reservoir.experiences[exp_idx]
        bullets = [source.bullets[b] for b in sorted(bullet_idxs)]
        experiences.append(
            TailoredExperience(
                source_experience_id=source.source_experience_id, bullets=bullets
            )
        )
    return TailoredCV(
        experiences=experiences,
        hard_skills=list(reservoir.hard_skills),
        soft_skills=list(reservoir.soft_skills),
        interests=list(reservoir.interests),
        focus=reservoir.focus,  # conserve le focus d'accroche au fil du remplissage
    )


def fill_to_one_page(
    base: ExperienceBase,
    reservoir: TailoredCV,
    profile: TemplateProfile | None = None,
    template: str = DEFAULT_TEMPLATE,
    photo: tuple[str, bytes] | None = None,
) -> tuple[TailoredCV, FillReport]:
    """Réduit le RÉSERVOIR `reservoir` au plus grand sous-ensemble tenant sur 1 page.

    `reservoir.experiences` est le matériel classé par l'appel A (ordre = pertinence).
    Les compétences éventuelles de `reservoir` sont conservées et présentes À CHAQUE
    mesure (elles occupent de la hauteur : on mesure le CV tel qu'il sera rendu).
    Retourne (cv_final, rapport). Ne lève pas sur « page pleine » ; propage en revanche
    une RenderError si le rendu lui-même échoue (réservoir non rendable, binaire absent).

    `photo` est transmise À CHAQUE mesure : une photo occupe de la hauteur dans la barre
    latérale, et la page doit être mesurée telle qu'elle sera rendue.
    """
    profile = profile or DEFAULT_PROFILE
    report = FillReport()
    exps = reservoir.experiences

    type_by_id = {exp.id: exp.type.value for exp in base.experiences}
    pinned_ids = {exp.id for exp in base.experiences if exp.pinned}

    def is_project(exp_idx: int) -> bool:
        return type_by_id.get(exps[exp_idx].source_experience_id) in PROJECT_TYPES

    def is_pinned(exp_idx: int) -> bool:
        return exps[exp_idx].source_experience_id in pinned_ids

    def count_projects(sel: dict[int, list[int]]) -> int:
        return sum(1 for i in sel if is_project(i))

    min_b = profile.min_bullets_per_entry
    max_b = profile.max_bullets_per_entry

    # --- 1. PLANCHER : stages épinglés + min_projects premiers projets ---------
    selected: dict[int, list[int]] = {}
    for idx, ex in enumerate(exps):
        if is_pinned(idx):
            selected[idx] = list(range(min(min_b, len(ex.bullets))))
    for idx, ex in enumerate(exps):
        if count_projects(selected) >= profile.min_projects:
            break
        if is_project(idx) and idx not in selected:
            selected[idx] = list(range(min(min_b, len(ex.bullets))))

    # Fichier PDF temporaire réutilisé pour toutes les mesures. Le typst empaqueté
    # (snap) n'écrit que sous la racine du projet : on place le temp sous output/
    # (relatif au répertoire de lancement).
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_pdf = tempfile.mkstemp(suffix=".pdf", prefix=".fill_", dir=out_dir)
    os.close(fd)

    def measure(sel: dict[int, list[int]]) -> int:
        render_cv(
            base, _build_cv(reservoir, sel), tmp_pdf, template=template, photo=photo
        )
        report.renders += 1
        return count_pdf_pages(tmp_pdf)

    try:
        # Mesure du plancher : s'il déborde déjà, on s'arrête là (rien à ajouter).
        if measure(selected) > 1:
            report.floor_overflow = True
        else:
            # --- 2. REMPLISSAGE glouton dans l'ordre du réservoir --------------
            candidates = [
                (exp_idx, b_idx)
                for exp_idx, ex in enumerate(exps)
                for b_idx in range(len(ex.bullets))
                if b_idx not in set(selected.get(exp_idx, []))
            ]
            overflow_started = False
            examined_after_overflow = 0
            for exp_idx, b_idx in candidates:
                # FENÊTRE de pertinence : une fois le 1er débordement passé, on n'examine
                # qu'au plus OVERFLOW_PROBE_WINDOW candidats de plus (positions dans le
                # classement), puis on arrête définitivement.
                if overflow_started:
                    if examined_after_overflow >= OVERFLOW_PROBE_WINDOW:
                        break
                    examined_after_overflow += 1

                current = selected.get(exp_idx, [])
                # Plafond bullets par entrée déjà ouverte.
                if current and len(current) >= max_b:
                    continue
                new_entry = exp_idx not in selected
                # On n'OUVRE une entrée qu'avec son TOUT PREMIER bullet (le plus pertinent).
                # Indispensable maintenant qu'on continue APRÈS un débordement : sans ce
                # garde, un bullet de rang > 0 pourrait rouvrir une entrée dont le 1er bullet
                # vient d'être retiré, en sautant le bullet le plus pertinent.
                if new_entry and b_idx != 0:
                    continue
                # Ouverture d'une nouvelle entrée : respecter le plafond de projets.
                if (
                    new_entry
                    and is_project(exp_idx)
                    and (count_projects(selected) >= profile.max_projects)
                ):
                    continue
                # Tentative : ajouter le candidat, rendre, mesurer.
                if overflow_started:
                    report.gap_probes += 1
                selected.setdefault(exp_idx, []).append(b_idx)
                if measure(selected) <= 1:
                    if overflow_started:
                        report.gap_fills += (
                            1  # un candidat plus court a comblé le creux
                        )
                    continue  # tient encore : on garde et on continue
                # Débordement : on retire ce candidat (retour à l'état 1 page précédent)...
                selected[exp_idx].pop()
                if new_entry:  # l'entrée a été ouverte par CE candidat -> la retirer
                    del selected[exp_idx]
                # ... et au lieu de S'ARRÊTER, on PASSE au suivant (dans la limite de la fenêtre).
                if not overflow_started:
                    overflow_started = True
                    report.stopped_on_overflow = True
    finally:
        Path(tmp_pdf).unlink(missing_ok=True)

    final_cv = _build_cv(reservoir, selected)
    report.final_entries = len(final_cv.experiences)
    report.final_bullets = sum(len(e.bullets) for e in final_cv.experiences)
    return final_cv, report
