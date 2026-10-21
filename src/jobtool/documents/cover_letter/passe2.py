"""PASSE 2 de la lettre : rédaction des paragraphes (1 appel LLM borné + boucle).

Prend la DÉCISION de la passe 1 (CoverLetterRoutingOutput) + la base + l'offre et produit
le TEXTE des paragraphes (CoverLetterDraftOutput). Ne rend AUCUN PDF (étape 4) et ne refait
PAS le routage : les deux passes sont ÉTANCHES — la passe 2 REÇOIT le routage, elle l'applique.

Réutilise la mécanique existante : core/llm/client (make_client) + core/llm/correction_loop
(run_correction_loop). Les bornes dures vivent dans cover_letter/draft_checks.py. Même schéma
de câblage que cv/generate.py et cover_letter/passe1.py.

Ancre de TON (anti « ton IA ») : on injecte, comme RÉFÉRENCE à NE PAS recopier, le contenu
d'un exemple de lettre (générique ou ciblé selon le mode). Le fichier peut être VIDE ou absent
-> on n'injecte simplement pas d'ancre et on NE PLANTE PAS.

LETTRE TYPE de la personne (profil › documents.letter_example) : si elle existe, elle
REMPLACE l'ancre de ton. Ce n'est plus une référence à ne pas recopier mais la BASE de la
lettre : le modèle en garde la structure et les phrases, et adapte ce qui dépend de l'offre.
Les bornes dures (longueurs, placeholders, chiffres de l'insert projet) restent les mêmes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.llm.client import RetryFn, make_client
from jobtool.core.llm.correction_loop import run_correction_loop
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.draft_checks import (
    achievement_text_by_id,
    build_correction,
    check_draft,
)
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)

# Exemples de ton = données projet éditables : relatifs au répertoire de lancement (cwd),
# comme les autres chemins data/ de l'outil. Jamais un chemin via __file__.
DEFAULT_EXAMPLES_DIR = Path("data/lettres_exemples")
EXAMPLE_FILES = {"generique": "exemple_generique.txt", "cible": "exemple_cible.txt"}

_SYSTEM_PROMPT = """\
Tu rédiges le corps d'une lettre de motivation en français, à partir d'une DÉCISION de
routage déjà prise (mode, projet, centre d'intérêt) et du matériel fourni. Tu produis UNIQUEMENT
les champs demandés ; tu ne rends aucun PDF et tu ne redécides pas du routage.

Structure française « Vous / Moi / Nous » :
- paragraphe_vous : l'entreprise et le domaine (ce qui t'attire concrètement) ;
- paragraphe_moi : qui tu es professionnellement, sans inventer ;
- paragraphe_nous : la rencontre (ce que tu apportes / la suite) ;
- formule : une formule de politesse brève.

TON — sobre, direct, humain. INTERDITS :
- les formules toutes faites (« C'est avec un vif intérêt… », « Fort de mon expérience… »,
  « Je me permets de… ») ;
- la flatterie de l'entreprise, l'enthousiasme vide, l'empilement d'adjectifs ;
- le remplissage : si tu n'as rien de précis à dire, fais court.
Écris comme une personne posée qui sait de quoi elle parle.

ANTI-FABRICATION (règle d'or) :
- Tu ne peux PARLER QUE de faits présents dans le matériel fourni. Aucun chiffre, techno ou
  résultat qui n'y figure pas.
- insert_projet : REFORMULE 1 à 2 des achievements RÉELS fournis (texte exact donné plus bas),
  sans ajouter de fait neuf. N'invente AUCUN nombre.
- insert_interet : appuie-toi sur les intitulés EXACTS de centres d'intérêt fournis ; tu peux
  les relier au domaine, sans inventer de fait sur la personne.
- EXCEPTION assumée : dans paragraphe_vous tu peux écrire UNE phrase libre sur l'entreprise/le
  domaine extraits de l'offre (texte non tracé, relu par l'humain). C'est la seule exception.

INSERTS — respecte EXACTEMENT la décision fournie :
- mode « generique » : insert_interet ET insert_projet valent null (aucun insert).
- mode « cible » : remplis insert_projet seulement si un projet est fourni, insert_interet
  seulement si un centre d'intérêt est fourni ; sinon le champ concerné vaut null.

Ne laisse JAMAIS de marqueur entre crochets « [comme ceci] » dans ta réponse : tout doit être
du texte définitif. (Les exemples de ton ci-dessous EN CONTIENNENT : ce sont des trous à NE PAS
recopier, juste une référence de style.)
"""


_EXAMPLE_RULES = """
LETTRE TYPE DE LA PERSONNE — elle est fournie plus bas et PRIME sur les règles de ton
ci-dessus. C'est la base de ta lettre :
- RECOPIE ses phrases MOT POUR MOT tant qu'elles restent vraies pour cette offre (ce qu'elle
  dit d'elle-même, sa formation, sa façon de travailler) : ne les reformule pas ;
- ne réécris QUE ce qui dépend de l'offre : entreprise, poste, domaine, missions, raisons de
  l'intérêt ;
- remplace tout nom d'entreprise, de poste ou détail propre à une autre candidature ;
- répartis son contenu dans paragraphe_vous / paragraphe_moi / paragraphe_nous, et reprends
  sa formule de politesse si elle en a une ;
- n'ajoute aucun fait sur la personne qui ne figure ni dans sa lettre ni dans le matériel.
"""


def _letter_example(base: ExperienceBase) -> str | None:
    """La lettre type du profil, ou None."""
    text = (base.documents.letter_example or "").strip()
    return text or None


def system_prompt(base: ExperienceBase) -> str:
    """Prompt système de la passe 2 : complété des règles de la lettre type s'il y en a une."""
    return _SYSTEM_PROMPT + (_EXAMPLE_RULES if _letter_example(base) else "")


def _load_tone_anchor(examples_dir: Path, mode: str) -> str | None:
    """Lit l'exemple de ton du mode ; None si fichier absent ou VIDE (cas géré, pas d'erreur)."""
    fname = EXAMPLE_FILES.get(mode)
    if fname is None:
        return None
    try:
        text = (examples_dir / fname).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    return text or None


def _build_context(
    base: ExperienceBase,
    routing: CoverLetterRoutingOutput,
    job_offer: str,
    examples_dir: Path,
) -> str:
    """Contexte LLM : décision passe 1 + textes EXACTS des achievements + intérêts + offre + ancre."""
    lines: list[str] = [
        "DÉCISION DE ROUTAGE (passe 1, à appliquer telle quelle) :",
        f"- mode : {routing.mode}",
        f"- entreprise (extraite de l'offre) : {routing.entreprise}",
        f"- domaine (extrait de l'offre) : {routing.domaine}",
    ]

    # Texte EXACT des achievements ciblés : le modèle reformule À PARTIR d'eux.
    text_by_id = achievement_text_by_id(base)
    lines.append("")
    if routing.projets_ids:
        lines.append(
            "ACHIEVEMENTS DU PROJET RETENU (reformule 1-2 d'entre eux pour insert_projet) :"
        )
        for pid in routing.projets_ids:
            lines.append(
                f"    • {pid} : {text_by_id.get(pid, '(introuvable)').strip()}"
            )
    else:
        lines.append("PROJET : aucun (insert_projet doit valoir null).")

    lines.append("")
    if routing.interets_ids:
        lines.append(
            "CENTRES D'INTÉRÊT RETENUS (intitulés exacts, pour insert_interet) :"
        )
        lines += [f"    • {iid}" for iid in routing.interets_ids]
    else:
        lines.append("CENTRE D'INTÉRÊT : aucun (insert_interet doit valoir null).")

    lines.append("")
    lines.append("OFFRE D'EMPLOI (texte brut) :")
    lines.append(job_offer)

    example = _letter_example(base)
    anchor = None if example else _load_tone_anchor(examples_dir, routing.mode)
    if example:
        lines.append("")
        lines.append(
            "LETTRE TYPE DE LA PERSONNE (base à adapter à cette offre, cf. consignes) :"
        )
        lines.append(example)
    if anchor:
        lines.append("")
        lines.append(
            "RÉFÉRENCE DE TON (NE recopie RIEN ; inspire-toi seulement du registre ; les "
            "crochets sont des trous, pas du texte à garder) :"
        )
        lines.append(anchor)

    lines.append("")
    style = (
        "en partant de la lettre type (adaptée à cette offre)"
        if example
        else "le ton sobre"
    )
    lines.append(
        f"Rédige les champs demandés en respectant la décision de routage, {style} et "
        "l'anti-fabrication. Pas de placeholders entre crochets dans ta réponse."
    )
    return "\n".join(lines)


def run_passe2(
    base: ExperienceBase,
    routing: CoverLetterRoutingOutput,
    job_offer: str,
    max_retries: int = 2,
    examples_dir: Path | None = None,
    on_attempt: Callable[[int, CoverLetterDraftOutput, list], None] | None = None,
    on_retry: RetryFn | None = None,
) -> tuple[CoverLetterDraftOutput, list[Violation]]:
    """Lance la passe 2 + sa boucle de correction. Retourne (draft, violations_restantes).

    Ne lève pas sur violations : l'appelant voit le rapport et décide (OK ssi vide), comme
    le CV et la passe 1. Le code ne réécrit jamais le draft : il détecte et redemande.

    `on_attempt` / `on_retry` absents (défaut) -> AUCUNE sortie (même règle que la passe 1).
    """
    examples_dir = examples_dir or DEFAULT_EXAMPLES_DIR
    client = make_client()
    messages = [
        {"role": "system", "content": system_prompt(base)},
        {
            "role": "user",
            "content": _build_context(base, routing, job_offer, examples_dir),
        },
    ]

    return run_correction_loop(
        client,
        messages,
        CoverLetterDraftOutput,
        check=lambda d: check_draft(base, routing, d),
        step="redaction",
        build_correction=build_correction,
        max_retries=max_retries,
        on_attempt=on_attempt,
        on_retry=on_retry,
    )
