"""Orchestration de la génération CV : utilise core/llm + core/traceability + cv.

Spécifique CV : QUELS schémas, QUELS checks (traçabilité + budget), QUELS system/user
prompts, et le garde-fou de faisabilité (pinned vs config). La mécanique d'appel Mistral
et la boucle de correction bornée vivent dans core/llm.

Entrée publique — generate_tailored_cv_checked : génération en DEUX appels LLM
indépendants, suivie d'un remplissage déterministe mesuré sur le PDF.
  Appel A — un RÉSERVOIR d'expériences/bullets CLASSÉ par pertinence (le modèle classe
    large ; il ne décide PAS combien tiennent sur la page). Boucle A : traçabilité
    expériences/bullets, longueur des bullets, plancher de projets.
  Appel B — compétences (hard/soft/interests) ordonnées par pertinence ; boucle B :
    traçabilité des libellés vs listes maîtres + plafonds de comptage.
  Corriger une famille ne peut casser l'autre (appels indépendants).
  Remplissage (cv/fill.py) — APRÈS les deux appels, le code part d'un plancher
    obligatoire puis ajoute les éléments du réservoir dans l'ordre de pertinence TANT
    QUE le PDF rendu tient sur 1 page : le PDF mesuré est la vraie borne. Les checks
    (traçabilité + budget) sont recalculés sur le CV FINAL rempli ; l'appelant voit le
    rapport et décide (OK ssi vide).

Les consignes au modèle sont des jugements (classer par pertinence) ; les bornes dures
restent vérifiées/appliquées par le code (consigne ex post).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.core.llm.client import RetryFn, make_client
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.core.traceability import Violation, check_traceability
from jobtool.documents.cv.budget import check_budget, check_config_feasible
from jobtool.documents.cv.fill import FillReport, fill_to_one_page
from jobtool.documents.cv.profiles import DEFAULT_PROFILE, TemplateProfile
from jobtool.documents.cv.render import (
    DEFAULT_TEMPLATE,
    RenderError,
    accroche_is_adaptable,
    validate_accroche_config,
)
from jobtool.documents.cv.schema import (
    TailoredCV,
    TailoredExperiencesOutput,
    TailoredSkillsOutput,
)

# --- Callbacks d'observabilité (no-op par défaut : ce module n'écrit jamais) -------------
#: Fin d'une tentative : (appel "A"|"B", n° de tentative, violations, dépassements).
#: On transmet les LISTES, pas un compte ni une phrase : l'interface décide de l'affichage.
CvAttemptFn = Callable[[str, int, list, list], None]
#: Issue du remplissage mesuré : (rapport, None) en cas de succès, (None, erreur) si le
#: réservoir n'a pas pu être rendu. Exactement l'un des deux est renseigné.
CvProgressFn = Callable[[FillReport | None, RenderError | None], None]

# ---------------------------------------------------------------------------
# System prompt — Appel A : contenu tracé (expériences + bullets)
# ---------------------------------------------------------------------------

_RULES_A = """\
Tu adaptes un CV à une offre d'emploi, à partir d'une base d'expériences fournie.
Tu produis UNIQUEMENT la liste des expériences (stages + projets) et leurs bullets reformulés.

Règles ESSENTIELLES, non négociables :
- Tu sélectionnes, réordonnes et reformules UNIQUEMENT des achievements présents dans la base fournie.
- Tu ne dois JAMAIS inventer d'expérience, d'achievement, de technologie ou de résultat
  qui ne figure pas dans la base.
- Chaque source_experience_id DOIT reprendre exactement un id d'expérience réel de la base,
  et chaque bullet.source_id DOIT reprendre exactement un id d'achievement réel de la base.
- Tu peux reformuler le texte d'un achievement pour le rapprocher de l'offre, mais sans
  ajouter le moindre fait nouveau (pas de techno, chiffre ou résultat absent de la base).
"""


def _build_system_prompt_a(profile: TemplateProfile, pinned: list[str]) -> str:
    # ── SECTION 1 : stages (toujours présents, jamais à décider) ───────────
    if pinned:
        stages_list = "\n".join(f"  - {item}" for item in pinned)
        stages_section = (
            f"\n── STAGES : toujours présents, jamais à sélectionner ───────────────────\n"
            f"Les {len(pinned)} stage(s) suivant(s) figurent OBLIGATOIREMENT dans le CV, sans exception :\n"
            f"{stages_list}\n"
            f"Ton seul travail sur ces stages : CLASSER leurs bullets par pertinence\n"
            f"DÉCROISSANTE pour l'offre (le plus pertinent d'abord) et les reformuler. Tu ne\n"
            f"décides PAS s'ils apparaissent — ils apparaissent toujours. Le système gardera\n"
            f"ensuite, en partant du haut de ton classement, autant de bullets qu'il en tient.\n"
        )
    else:
        stages_section = ""

    # ── SECTION 2 : projets = un RÉSERVOIR classé, pas une sélection finale ──
    projects_section = (
        f"\n── PROJETS : fournis un RÉSERVOIR classé (PAS une sélection finale) ─────\n"
        f"En plus des stages, CLASSE par pertinence DÉCROISSANTE pour l'offre TOUS les\n"
        f"projets que tu juges pertinents — ne te limite PAS à un nombre précis de projets.\n"
        f"Fournis-en au moins {profile.min_projects} (et idéalement davantage, pour laisser du\n"
        f"choix au système). Pour CHAQUE projet, classe aussi ses bullets pertinents du plus\n"
        f"au moins pertinent.\n"
        f"\nTu ne décides PAS combien d'entrées ou de bullets tiennent sur la page : le système\n"
        f"partira du HAUT de ton classement et gardera autant d'entrées et de bullets qu'il en\n"
        f"tient sur UNE seule page. L'ORDRE que tu donnes EST ta recommandation de priorité —\n"
        f"c'est lui qui sera suivi, alors soigne-le.\n"
    )

    # ── SECTION 3 : principe de classement + limites par bullet ─────────────
    rules_section = (
        f"\n── PRINCIPE DE CLASSEMENT & LIMITES ─────────────────────────────────────\n"
        f"- « Bullets d'abord » : ne fais figurer une entrée que si elle a au moins\n"
        f"  {profile.min_bullets_per_entry} bullet vraiment pertinent pour l'offre.\n"
        f"- Mets en tête les projets les plus pertinents pour l'offre ; ensuite, valorise la\n"
        f"  DIVERSITÉ (un projet au registre technique complémentaire est un atout). Priorité\n"
        f"  au concret lié à l'offre, puis à la variété.\n"
        f"- au plus {profile.max_bullets_per_entry} bullets par entrée ;\n"
        f"- au plus {profile.max_bullet_chars} caractères par bullet.\n"
    )

    return _RULES_A + stages_section + projects_section + rules_section


# ---------------------------------------------------------------------------
# System prompt — Appel B : sélections de listes (compétences + intérêts)
# ---------------------------------------------------------------------------

_RULES_B = """\
Tu sélectionnes des compétences et centres d'intérêt pour un CV adapté à une offre d'emploi.
Tu travailles UNIQUEMENT sur hard_skills, soft_skills et interests.

Règles ESSENTIELLES, non négociables :
- Tu choisis UNIQUEMENT parmi les listes maîtres fournies (hard_skills, soft_skills, interests).
- Tu recopies les intitulés EXACTEMENT tels qu'ils figurent dans les listes, sans inventer,
  sans reformuler, sans raccourcir, sans fusionner.
- Tu classes chaque liste par pertinence DÉCROISSANTE pour l'offre (le plus pertinent en premier).
- Tu ne sélectionnes PAS plus d'éléments que les plafonds indiqués.
"""


def _build_system_prompt_b(
    profile: TemplateProfile, request_focus: bool = False
) -> str:
    limits = (
        f"\nPlafonds à respecter IMPÉRATIVEMENT :\n"
        f"- au plus {profile.max_hard_skills} hard skills ;\n"
        f"- au plus {profile.max_soft_skills} soft skills ;\n"
        f"- au plus {profile.max_interests} centres d'intérêt.\n"
    )
    # Consigne focus AJOUTÉE seulement quand une accroche adaptable est active : on
    # demande juste le texte du trou {focus}, borné, sans réécrire la phrase.
    focus_rule = ""
    if request_focus:
        focus_rule = (
            "\nEn plus, propose un champ `focus` : un court groupe nominal (2 à 6 mots) à\n"
            "insérer dans la phrase d'accroche du candidat à la place du trou {focus}, le plus\n"
            "pertinent pour CETTE offre. Réponds uniquement par ce groupe de mots (pas de phrase\n"
            "complète, pas de ponctuation finale). N'écris RIEN d'autre dans `focus`.\n"
        )
    return _RULES_B + limits + focus_rule


# ---------------------------------------------------------------------------
# User prompts
# ---------------------------------------------------------------------------


def _pinned_summary(base: ExperienceBase) -> list[str]:
    return [f"{exp.id} — {exp.title}" for exp in base.experiences if exp.pinned]


def _build_user_prompt_a(base: ExperienceBase, job_offer: str) -> str:
    """Prompt initial appel A : base complète + offre."""
    return (
        "Voici ma base d'expériences (JSON) :\n"
        f"{base.model_dump_json(indent=2)}\n\n"
        "Voici l'offre d'emploi (texte brut) :\n"
        f"{job_offer}\n\n"
        "Produis la liste des expériences adaptées à cette offre en respectant strictement les règles."
    )


def _build_user_prompt_b(
    base: ExperienceBase, job_offer: str, accroche_template: str | None = None
) -> str:
    """Prompt initial appel B : listes maîtres + offre (pas les expériences).

    Si `accroche_template` est fourni (accroche adaptable active), on montre la phrase à
    compléter pour que le modèle choisisse un `focus` pertinent ; sinon rien n'est ajouté.
    """
    lists = {
        "hard_skills": base.hard_skills,
        "soft_skills": base.soft_skills,
        "interests": base.interests,
    }
    accroche_block = ""
    if accroche_template is not None:
        accroche_block = (
            "\n\nPhrase d'accroche à compléter (insère ton `focus` à la place de {focus}, "
            "ne réécris pas le reste) :\n"
            f"{accroche_template}"
        )
    return (
        "Voici les listes maîtres de compétences et centres d'intérêt (JSON) :\n"
        f"{json.dumps(lists, ensure_ascii=False, indent=2)}\n\n"
        "Voici l'offre d'emploi (texte brut) :\n"
        f"{job_offer}"
        f"{accroche_block}\n\n"
        "Sélectionne et classe les compétences et centres d'intérêt les plus pertinents "
        "pour cette offre en respectant strictement les règles."
    )


# ---------------------------------------------------------------------------
# Correction prompts
# ---------------------------------------------------------------------------


def _split(issues: list) -> tuple[list[Violation], list[str]]:
    violations = [i for i in issues if isinstance(i, Violation)]
    overruns = [i for i in issues if not isinstance(i, Violation)]
    return violations, overruns


def _build_correction_prompt_a(issues: list) -> str:
    violations, overruns = _split(issues)
    lines: list[str] = ["Ton réservoir d'expériences précédent doit être corrigé."]
    if violations:
        lines += ["", "Violations de traçabilité :"]
        lines += [f"- {v}" for v in violations]
    if overruns:
        lines += ["", "Autres problèmes :"]
        lines += [f"- {o}" for o in overruns]
    lines += [
        "",
        "Corrige le réservoir : n'utilise QUE des ids réels présents dans la base fournie "
        "plus haut (source_experience_id et bullet.source_id), sans inventer ni expérience, "
        "ni achievement, ni technologie, ni résultat ; garde chaque bullet sous la limite de "
        "caractères ; classe par pertinence décroissante. Renvoie un réservoir complet.",
    ]
    return "\n".join(lines)


def _build_correction_prompt_b(issues: list) -> str:
    violations, overruns = _split(issues)
    lines: list[str] = ["Ta sélection de compétences précédente doit être corrigée."]
    if violations:
        lines += ["", "Violations de traçabilité :"]
        lines += [f"- {v}" for v in violations]
    if overruns:
        lines += ["", "Dépassements de comptage :"]
        lines += [f"- {o}" for o in overruns]
    lines += [
        "",
        "Corrige la sélection : n'utilise QUE des intitulés EXACTEMENT identiques "
        "aux listes maîtres fournies plus haut, sans inventer ni reformuler ni "
        "raccourcir ; classe-les du plus au moins pertinent pour l'offre ; "
        "respecte les plafonds de nombre.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Check functions for each call
# ---------------------------------------------------------------------------


def _check_call_a(
    base: ExperienceBase,
    result: TailoredExperiencesOutput,
    profile: TemplateProfile,
) -> list:
    """Vérifie les contraintes de l'appel A — appliquées au RÉSERVOIR, pas à une sélection.

    Le réservoir est volontairement LARGE (le code coupera ensuite ce qui tient sur la
    page). On NE vérifie donc PAS ici le nombre maximal de projets ni un budget total de
    bullets : ces bornes-là sont gérées par le remplissage mesuré (cv/fill.py) puis par
    check_budget sur le RÉSULTAT FINAL. On vérifie seulement ce que le modèle SEUL peut
    garantir et que le code ne peut pas réparer :
      - traçabilité (ids réels, pas de doublon, stages épinglés présents) ;
      - longueur de chaque bullet (le code ne raccourcit jamais un bullet) ;
      - un PLANCHER de projets (assez de matériel pour atteindre min_projets).
    """
    proxy = TailoredCV(experiences=result.experiences)
    issues: list = list(check_traceability(base, proxy))

    for ex in result.experiences:
        for bullet in ex.bullets:
            length = len(bullet.text)
            if length > profile.max_bullet_chars:
                issues.append(
                    f"bullet trop longue ({length}>{profile.max_bullet_chars}) : "
                    f"{bullet.text[:40]}…"
                )

    type_by_id = {exp.id: exp.type.value for exp in base.experiences}
    num_projects = sum(
        1
        for ex in result.experiences
        if type_by_id.get(ex.source_experience_id) in PROJECT_TYPES
    )
    if num_projects < profile.min_projects:
        issues.append(
            f"réservoir trop pauvre : {num_projects} projet(s) < {profile.min_projects} "
            f"minimum (fournis-en davantage pour laisser le système choisir)"
        )
    return issues


def _check_call_b(
    base: ExperienceBase,
    result: TailoredSkillsOutput,
    profile: TemplateProfile,
) -> list:
    """Vérifie les contraintes de l'appel B (listes de compétences uniquement)."""
    issues: list = []
    master_hard = set(base.hard_skills)
    for skill in result.hard_skills:
        if skill not in master_hard:
            issues.append(
                Violation(
                    kind="invented_hard_skill", message=f"hard skill inventé : {skill}"
                )
            )
    master_soft = set(base.soft_skills)
    for skill in result.soft_skills:
        if skill not in master_soft:
            issues.append(
                Violation(
                    kind="invented_soft_skill", message=f"soft skill inventé : {skill}"
                )
            )
    master_interests = set(base.interests)
    for interest in result.interests:
        if interest not in master_interests:
            issues.append(
                Violation(
                    kind="invented_interest",
                    message=f"centre d'intérêt inventé : {interest}",
                )
            )
    if len(result.hard_skills) > profile.max_hard_skills:
        issues.append(
            f"trop de hard skills : {len(result.hard_skills)} > {profile.max_hard_skills} autorisés"
        )
    if len(result.soft_skills) > profile.max_soft_skills:
        issues.append(
            f"trop de soft skills : {len(result.soft_skills)} > {profile.max_soft_skills} autorisés"
        )
    if len(result.interests) > profile.max_interests:
        issues.append(
            f"trop de centres d'intérêt : {len(result.interests)} > {profile.max_interests} autorisés"
        )
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_tailored_cv_checked(
    base: ExperienceBase,
    job_offer: str,
    max_retries: int = 2,
    profile: TemplateProfile | None = None,
    template: str = DEFAULT_TEMPLATE,
    on_attempt: CvAttemptFn | None = None,
    on_progress: CvProgressFn | None = None,
    on_retry: RetryFn | None = None,
) -> tuple[TailoredCV, list[Violation], list[str]]:
    """Génère un CV en DEUX appels LLM indépendants, puis le REMPLIT à la page mesurée.

    Appel A : un RÉSERVOIR d'expériences/bullets classé par pertinence (le modèle ne
              décide PAS combien tiennent sur la page ; il classe large). Boucle A :
              traçabilité, longueur des bullets, plancher de projets.
    Appel B : hard/soft/interests ordonnés (traçabilité listes maîtres, comptages).
    Corriger une famille ne peut casser l'autre (appels indépendants).

    Remplissage (cv/fill.py) : APRÈS les deux appels, le code part du plancher obligatoire
    (3 stages + min_projects projets, chacun avec min_bullets), puis ajoute les éléments
    du réservoir un à un dans l'ordre de pertinence TANT QUE le PDF rendu (avec le
    `template` choisi) tient sur 1 page. Le PDF mesuré est la vraie borne.

    Retourne (cv_final, violations_traçabilité, dépassements_budget) — checks recalculés
    sur le CV FINAL rempli. Ne lève pas : l'appelant voit le rapport et décide.

    Observabilité (tous optionnels, no-op par défaut -> ce module N'ÉCRIT RIEN de lui-même) :
      - `on_attempt("A"|"B", tentative, violations, dépassements)` après chaque tentative ;
      - `on_progress(rapport_de_remplissage, erreur_de_rendu)` une fois le remplissage tenté
        (exactement l'un des deux est renseigné) ;
      - `on_retry` relayé au transport pour les attentes après un 429.
    Ces callbacks reçoivent des DONNÉES, jamais des phrases : la mise en forme appartient à
    la couche d'interface (la CLI les rebranche ; l'API n'en branche aucun).
    """
    profile = profile or DEFAULT_PROFILE
    check_config_feasible(base, profile)
    # Garde-fou de config d'accroche AVANT tout appel LLM (lève si show_accroche=true mais
    # base mal formée : ni accroche ni accroche_template, ou accroche_template sans {focus}).
    validate_accroche_config(base, profile)
    # Focus demandé à l'appel B UNIQUEMENT si l'accroche adaptable est active sur ce template.
    request_focus = accroche_is_adaptable(base, profile)
    client = make_client()

    # --- Appel A : expériences ------------------------------------------------
    messages_a = [
        {
            "role": "system",
            "content": _build_system_prompt_a(profile, _pinned_summary(base)),
        },
        {"role": "user", "content": _build_user_prompt_a(base, job_offer)},
    ]

    def _on_attempt_a(
        attempt: int, result: TailoredExperiencesOutput, issues: list
    ) -> None:
        violations, overruns = _split(issues)
        on_attempt("A", attempt, violations, overruns)  # type: ignore[misc]

    result_a, issues_a = run_correction_loop(
        client,
        messages_a,
        TailoredExperiencesOutput,
        check=lambda r: _check_call_a(base, r, profile),
        step="cv_a",
        build_correction=_build_correction_prompt_a,
        max_retries=max_retries,
        on_attempt=_on_attempt_a if on_attempt is not None else None,
        on_retry=on_retry,
    )

    # --- Appel B : compétences (+ focus d'accroche si adaptable) --------------
    accroche_template = base.profile.accroche_template if request_focus else None
    messages_b = [
        {"role": "system", "content": _build_system_prompt_b(profile, request_focus)},
        {
            "role": "user",
            "content": _build_user_prompt_b(base, job_offer, accroche_template),
        },
    ]

    def _on_attempt_b(attempt: int, result: TailoredSkillsOutput, issues: list) -> None:
        violations, overruns = _split(issues)
        on_attempt("B", attempt, violations, overruns)  # type: ignore[misc]

    result_b, issues_b = run_correction_loop(
        client,
        messages_b,
        TailoredSkillsOutput,
        check=lambda r: _check_call_b(base, r, profile),
        step="cv_b",
        build_correction=_build_correction_prompt_b,
        max_retries=max_retries,
        on_attempt=_on_attempt_b if on_attempt is not None else None,
        on_retry=on_retry,
    )

    # --- Assemblage du RÉSERVOIR (experiences de A, compétences + focus de B) --
    # `focus` (texte du trou d'accroche) n'est rempli que si on l'a demandé ; vide sinon.
    reservoir_cv = TailoredCV(
        experiences=result_a.experiences,
        hard_skills=result_b.hard_skills,
        soft_skills=result_b.soft_skills,
        interests=result_b.interests,
        focus=result_b.focus if request_focus else "",
    )

    # --- Remplissage mesuré sur le PDF (le code coupe ce qui tient sur 1 page) -
    # Si le réservoir n'est pas rendable (ex : un id d'expérience invraisonnable a
    # survécu aux retries -> render lèverait), on NE remplit pas : on conserve le
    # réservoir tel quel et on laisse les checks le signaler (jamais un CV faussé).
    try:
        final_cv, report = fill_to_one_page(
            base, reservoir_cv, profile=profile, template=template
        )
        if on_progress is not None:
            on_progress(report, None)
    except RenderError as exc:
        if on_progress is not None:
            on_progress(None, exc)
        final_cv = reservoir_cv

    # --- Checks 100 % code sur le CV FINAL (traçabilité + budget) --------------
    violations = list(check_traceability(base, final_cv))
    overruns = list(check_budget(base, final_cv, profile))
    return final_cv, violations, overruns
