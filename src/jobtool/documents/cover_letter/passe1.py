"""PASSE 1 de la lettre : routage + extraction + sélection d'ids tracés (1 appel LLM borné).

Ne génère AUCUN texte de lettre, ne rend AUCUN PDF, ne mentionne aucun paragraphe : produit
seulement une décision structurée (CoverLetterRoutingOutput) à partir de la base + l'offre.

Réutilise la mécanique existante : core/llm/client (structured output + retry réseau) et
core/llm/correction_loop.run_correction_loop (boucle de correction bornée). Les bornes dures
vivent dans cover_letter/routing.py. Même schéma de câblage que cv/generate.py.
"""

from __future__ import annotations

from collections.abc import Callable

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.core.llm.client import RetryFn, make_client
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.routing import build_correction, check_routing
from jobtool.documents.cover_letter.schema import CoverLetterRoutingOutput

_SYSTEM_PROMPT = """\
Tu prépares le ROUTAGE d'une lettre de motivation à partir d'une base de profil et d'une
offre d'emploi. Tu NE rédiges AUCUN texte de lettre : tu produis seulement une décision.

Tu dois :
1. Extraire de l'OFFRE le nom de l'entreprise (champ « entreprise ») et le domaine du poste
   (champ « domaine », un groupe nominal court, ex. « calcul haute performance »). Ces deux
   champs viennent de l'offre ; garde-les courts et sur une seule ligne.
2. Décider du « mode » :
   - « cible » s'il existe dans la base du matériel PERTINENT pour l'offre : au moins un
     achievement de PROJET et/ou un centre d'intérêt clairement lié à l'offre ;
   - « generique » sinon.
3. Sélectionner UNIQUEMENT des ids EXISTANTS, recopiés EXACTEMENT depuis le contexte fourni :
   - projets_ids : des source_id d'achievements de PROJET au format « projet-xxx.N »
     (JAMAIS l'id d'expérience « projet-xxx » sans suffixe .N) justifiant un insert « projet » ;
   - interets_ids : des intitulés EXACTS de centres d'intérêt justifiant un insert « intérêt ».
   Tu ne dois JAMAIS inventer d'id. En mode « generique », projets_ids et interets_ids sont VIDES.
   En mode « cible », au moins un id valide est requis.
   Pour projets_ids, ne retiens qu'UN SEUL projet (le plus pertinent pour l'offre) ; tu peux en
   citer plusieurs achievements, mais ils doivent tous appartenir à ce même projet. (Les centres
   d'intérêt ne sont pas concernés par cette limite.)

Tu choisis dans la liste fournie ; tu ne rédiges pas de phrases, tu ne mentionnes pas le poste.
"""


def _build_context(base: ExperienceBase) -> str:
    """Liste les PROJETS (intitulé + achievements .N choisissables) et les centres d'intérêt.

    On N'AFFICHE PLUS l'id d'EXPÉRIENCE nu (« projet-xxx ») : les SEULS jetons d'id présentés
    sont les source_id d'achievement au format « projet-xxx.N » — exactement ce que la borne
    accepte. Évite que le LLM recopie l'id d'expérience (cause systémique de rejets en boucle).
    """
    lines: list[str] = [
        "PROJETS DISPONIBLES — choisis UNIQUEMENT des source_id d'achievement au format "
        "« projet-xxx.N » listés ci-dessous (jamais un id d'expérience « projet-xxx » nu) :"
    ]
    has_project = False
    for exp in base.experiences:
        if exp.type.value not in PROJECT_TYPES:
            continue
        has_project = True
        lines.append(f"- Projet « {exp.title} » — achievements sélectionnables :")
        for ach in exp.achievements:
            lines.append(f"    • source_id={ach.id} : {ach.text.strip()}")
    if not has_project:
        lines.append("- (aucun projet dans la base)")

    lines.append("")
    lines.append("CENTRES D'INTÉRÊT DISPONIBLES (l'intitulé EXACT est l'id) :")
    if base.interests:
        lines += [f"- {interet}" for interet in base.interests]
    else:
        lines.append("- (aucun centre d'intérêt dans la base)")
    return "\n".join(lines)


def _build_user_prompt(base: ExperienceBase, job_offer: str) -> str:
    """Contexte (projets + intérêts disponibles) + offre brute."""
    return (
        "Voici le matériel disponible dans la base :\n"
        f"{_build_context(base)}\n\n"
        "Voici l'offre d'emploi (texte brut) :\n"
        f"{job_offer}\n\n"
        "Produis la décision de routage en respectant strictement les règles "
        "(ids existants uniquement, cohérence du mode, entreprise/domaine courts)."
    )


def run_passe1(
    base: ExperienceBase,
    job_offer: str,
    max_retries: int = 2,
    on_attempt: Callable[[int, CoverLetterRoutingOutput, list], None] | None = None,
    on_retry: RetryFn | None = None,
) -> tuple[CoverLetterRoutingOutput, list[Violation]]:
    """Lance la passe 1 + sa boucle de correction. Retourne (décision, violations_restantes).

    Ne lève pas sur violations : l'appelant voit le rapport et décide (OK ssi vide), comme
    le CV. Le code ne réécrit jamais la décision : il détecte et redemande au modèle.

    `on_attempt` / `on_retry` absents (défaut) -> AUCUNE sortie : la progression est
    l'affaire de la couche d'interface, pas de ce module.
    """
    client = make_client()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(base, job_offer)},
    ]

    output, issues = run_correction_loop(
        client,
        messages,
        CoverLetterRoutingOutput,
        check=lambda o: check_routing(base, o),
        step="routage",
        build_correction=lambda iss: build_correction(base, iss),
        max_retries=max_retries,
        on_attempt=on_attempt,
        on_retry=on_retry,
    )
    return output, issues
