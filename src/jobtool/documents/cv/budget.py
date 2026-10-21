"""Budget DUR du CV, vérifié par le code (consigne ex post), piloté par un PROFIL.

check_budget est GÉNÉRIQUE : il prend un TemplateProfile (cf. cv/profiles.py) et
applique ses règles — aucune limite en dur dans le corps. Il retourne la liste
lisible des dépassements (vide = OK), comme check_traceability. Il NE LÈVE PAS :
la boucle de generate.py renvoie les dépassements au modèle pour correction.

Le code ne fait que VÉRIFIER des bornes ; il n'impose aucune stratégie de sélection
(celle-ci est décrite au modèle dans le system prompt).
"""

from __future__ import annotations

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.documents.cv.profiles import DEFAULT_PROFILE, TemplateProfile
from jobtool.documents.cv.schema import TailoredCV


class ConfigError(RuntimeError):
    """Configuration impossible à satisfaire (ex : trop d'épinglées pour le budget)."""


def check_config_feasible(
    base: ExperienceBase, profile: TemplateProfile | None = None
) -> None:
    """Vérifie que la config est satisfiable AVANT toute génération.

    Si plus d'expériences sont épinglées que le template ne peut afficher d'entrées
    (plus grande clé de la table bullet_budget), aucune génération ne peut réussir :
    on LÈVE ConfigError tout de suite (pas de boucle, pas d'appel LLM). C'est
    volontairement une exception : le modèle ne peut rien y faire, c'est à l'humain.
    """
    profile = profile or DEFAULT_PROFILE
    pinned = [exp.id for exp in base.experiences if exp.pinned]
    max_entries = max(profile.bullet_budget) if profile.bullet_budget else len(pinned)
    if len(pinned) > max_entries:
        raise ConfigError(
            f"Config impossible : {len(pinned)} expériences épinglées mais le template "
            f"n'affiche au plus que {max_entries} entrées. Réduis les épinglées ou "
            f"élargis le budget du template."
        )

    # Le template exige au moins min_projects PROJETS : si la base n'en fournit pas
    # assez, aucune génération ne peut satisfaire le plancher -> on lève tout de suite.
    available_projects = sum(
        1 for exp in base.experiences if exp.type.value in PROJECT_TYPES
    )
    if available_projects < profile.min_projects:
        raise ConfigError(
            f"Config impossible : {available_projects} projet(s) disponible(s) dans la base "
            f"mais le template en exige au moins {profile.min_projects}. Ajoute des projets "
            f"à la base ou abaisse min_projects du template."
        )


def check_budget(
    base: ExperienceBase, cv: TailoredCV, profile: TemplateProfile | None = None
) -> list[str]:
    """Retourne la liste lisible des dépassements de budget (vide = dans les clous)."""
    profile = profile or DEFAULT_PROFILE
    overruns: list[str] = []

    type_by_id = {exp.id: exp.type.value for exp in base.experiences}
    title_by_id = {exp.id: exp.title for exp in base.experiences}

    num_projects = sum(
        1
        for ex in cv.experiences
        if type_by_id.get(ex.source_experience_id) in PROJECT_TYPES
    )

    # --- Nombre de projets (borné des DEUX côtés : {min..max}) -----------------
    # Les stages épinglés ne comptent pas ici : c'est bien le nb de PROJETS qui est borné.
    if num_projects < profile.min_projects:
        overruns.append(
            f"trop peu de projets : {num_projects} < {profile.min_projects} minimum"
        )
    if num_projects > profile.max_projects:
        overruns.append(
            f"trop de projets : {num_projects} > {profile.max_projects} autorisés"
        )

    # NB : il n'y a PLUS de « budget global de bullets » (table bullet_budget) vérifié
    # ici. Depuis le remplissage mesuré (cv/fill.py), la vraie borne du nombre total de
    # bullets est le PDF rendu sur 1 page (check_single_page). Une table approximative
    # contredirait cette mesure. bullet_budget ne sert plus qu'à check_config_feasible
    # (borne haute du nombre d'entrées affichables, garde-fou de configuration).

    # --- Bornes de bullets PAR entrée -----------------------------------------
    for ex in cv.experiences:
        n = len(ex.bullets)
        if n < profile.min_bullets_per_entry:
            overruns.append(
                f"entrée {ex.source_experience_id} : {n} bullet(s) "
                f"< {profile.min_bullets_per_entry} minimum"
            )
        if n > profile.max_bullets_per_entry:
            overruns.append(
                f"entrée {ex.source_experience_id} : {n} bullets "
                f"> {profile.max_bullets_per_entry} autorisées"
            )

    # --- Limites de caractères : titres + bullets ------------------------------
    for ex in cv.experiences:
        title = title_by_id.get(ex.source_experience_id)
        if title is not None and len(title) > profile.max_title_chars:
            overruns.append(
                f"titre trop long ({len(title)}>{profile.max_title_chars}) : {title}"
            )
        for bullet in ex.bullets:
            length = len(bullet.text)
            if length > profile.max_bullet_chars:
                overruns.append(
                    f"bullet trop longue ({length}>{profile.max_bullet_chars}) : "
                    f"{bullet.text[:40]}…"
                )

    # NB : la longueur des intitulés de FORMATION n'est PAS vérifiée. La formation
    # est recopiée telle quelle (jamais générée par le LLM) : une violation dessus
    # serait incorrigeable par la boucle de correction (le LLM n'a pas la main).

    # NB : la longueur des LIGNES de compétences (hard/soft/interests) n'est PAS
    # vérifiée ici : le troncage déterministe dans render.py la rend structurellement
    # impossible à dépasser. max_skills_line_chars pilote ce troncage au rendu.

    # --- Comptages de compétences ---------------------------------------------
    if len(cv.soft_skills) > profile.max_soft_skills:
        overruns.append(
            f"trop de soft skills : {len(cv.soft_skills)} > {profile.max_soft_skills} autorisés"
        )
    if len(cv.hard_skills) > profile.max_hard_skills:
        overruns.append(
            f"trop de hard skills : {len(cv.hard_skills)} > {profile.max_hard_skills} autorisés"
        )
    if len(cv.interests) > profile.max_interests:
        overruns.append(
            f"trop de centres d'intérêt : {len(cv.interests)} > {profile.max_interests} autorisés"
        )

    return overruns
