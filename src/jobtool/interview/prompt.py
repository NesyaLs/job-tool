"""Construction de ce qui part au LLM : prompt système, état de l'entretien, historique.

Le prompt de l'intervieweur vit dans `prompts/interviewer.<langue>.md` (versionné, lisible
tel quel) et se lit par `importlib.resources`. La langue est un paramètre avec le français
pour seule valeur aujourd'hui : ajouter une langue, c'est ajouter un fichier, pas du code.

Maîtrise du coût : seuls les messages postérieurs au résumé partent au LLM. Quand ils
dépassent `HISTORY_WINDOW + SUMMARY_EVERY`, les plus anciens sont condensés en un résumé
(appel séparé), et il n'en reste que `HISTORY_WINDOW`. Où couper est décidé ici, par le code.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.interview.gaps import Gap
from jobtool.interview.models import SECTION_LABELS, SECTION_ORDER, InterviewSession

#: Messages récents envoyés tels quels au LLM (au minimum, après un résumé).
HISTORY_WINDOW = 16
#: Messages supplémentaires tolérés avant de déclencher un nouveau résumé.
SUMMARY_EVERY = 12

#: Refus récents rappelés au LLM (au-delà, l'état grossirait sans utilité).
MAX_REJECTED_SHOWN = 15

DEFAULT_LANGUAGE = "fr"


@cache
def system_prompt(language: str = DEFAULT_LANGUAGE) -> str:
    """Texte du prompt système de l'intervieweur (fichier de paquet)."""
    resource = files("jobtool.interview").joinpath(
        "prompts", f"interviewer.{language}.md"
    )
    return resource.read_text(encoding="utf-8")


def compact_profile(base: ExperienceBase | None) -> dict:
    """Le profil sous une forme compacte, AVEC les ids que le LLM peut citer."""
    if base is None:
        return {"vide": True}
    p = base.profile
    return {
        "identite": {
            "name": p.name,
            "email": p.email,
            "phone": p.phone,
            "location": p.location,
            "links": p.links or [],
        },
        "formation": [
            {"degree": e.degree, "school": e.school, "start": e.start, "end": e.end}
            for e in base.education
        ],
        "langues": [
            {"name": lang.name, "level": lang.level} for lang in base.languages
        ],
        "experiences": [
            {
                "id": e.id,
                "type": e.type.value,
                "title": e.title,
                "organization": e.organization,
                "start": e.start,
                "end": e.end,
                "summary": e.summary,
                "realisations": [
                    {"id": a.id, "text": a.text, "skills": a.skills}
                    for a in e.achievements
                ],
            }
            for e in base.experiences
        ],
        "hard_skills": base.hard_skills,
        "soft_skills": base.soft_skills,
    }


def state_block(
    session: InterviewSession, base: ExperienceBase | None, gaps: list[Gap]
) -> str:
    """L'état de l'entretien, tel que le LLM le lit à chaque tour."""
    plan = [
        {"section": s.value, "titre": SECTION_LABELS[s], "statut": session.sections[s]}
        for s in SECTION_ORDER
    ]
    pending = [
        {"id": p.id, "kind": p.kind.value, **p.payload} for p in session.pending()
    ]
    state = {
        "section_en_cours": session.current.value,
        "plan": plan,
        "manques_detectes": [
            {"section": g.section.value, "manque": g.message, "cible": g.target_id}
            for g in gaps
        ],
        "contexte": session.context_notes,
        "profil_actuel": compact_profile(base),
        "propositions_en_attente": pending,
        # Les refus récents : le LLM ne doit pas reproposer ce que la personne a écarté.
        "propositions_refusees": [
            {"kind": p.kind.value, **p.payload}
            for p in session.proposals
            if p.status == "rejected"
        ][-MAX_REJECTED_SHOWN:],
        "prochain_id_de_proposition": f"p{session.proposal_seq + 1}",
    }
    parts = ["# État de l'entretien", json.dumps(state, ensure_ascii=False, indent=1)]
    if session.summary:
        parts += ["# Résumé du début de l'entretien", session.summary]
    return "\n\n".join(parts)


def build_messages(
    session: InterviewSession,
    base: ExperienceBase | None,
    gaps: list[Gap],
    language: str = DEFAULT_LANGUAGE,
) -> list[dict]:
    """Messages d'un tour : système (prompt + état), puis l'historique non résumé.

    Les messages de l'utilisateur sont préfixés de leur id (« [m3] ») : c'est ce que le LLM
    cite dans `sources`, et ce que le code vérifie.
    """
    messages: list[dict] = [
        {
            "role": "system",
            "content": system_prompt(language)
            + "\n\n"
            + state_block(session, base, gaps),
        }
    ]
    for m in session.messages[session.summary_upto :]:
        if m.role == "user":
            messages.append({"role": "user", "content": f"[{m.id}] {m.text}"})
        else:
            messages.append({"role": "assistant", "content": m.text})
    return messages


def summary_cut(session: InterviewSession) -> int | None:
    """Index jusqu'auquel résumer, ou None si l'historique tient encore dans la fenêtre.

    La coupe tombe juste avant un message UTILISATEUR, pour que l'historique envoyé ne
    commence jamais par une réponse de l'assistant orpheline de sa question.
    """
    unsummarized = len(session.messages) - session.summary_upto
    if unsummarized <= HISTORY_WINDOW + SUMMARY_EVERY:
        return None
    cut = len(session.messages) - HISTORY_WINDOW
    while cut > session.summary_upto and session.messages[cut].role != "user":
        cut -= 1
    return cut if cut > session.summary_upto else None


def summary_messages(session: InterviewSession, cut: int) -> list[dict]:
    """Demande de résumé des messages [summary_upto, cut), à fusionner avec l'ancien."""
    transcript = "\n".join(
        f"{'Personne' if m.role == 'user' else 'Intervieweur'} [{m.id}] : {m.text}"
        for m in session.messages[session.summary_upto : cut]
    )
    instructions = (
        "Tu résumes un entretien de construction de profil professionnel, pour que "
        "l'intervieweur puisse le poursuivre sans relire les messages. Garde les FAITS "
        "donnés par la personne (avec les ids de messages entre crochets), les questions "
        "restées sans réponse et ce qu'elle a refusé. N'ajoute rien. 15 lignes au plus."
    )
    previous = f"Résumé précédent :\n{session.summary}\n\n" if session.summary else ""
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": f"{previous}Messages à intégrer :\n{transcript}"},
    ]


def correction_message(issues: list[str]) -> str:
    """Message renvoyé au LLM quand le code a trouvé des problèmes dans sa réponse."""
    lines = "\n".join(f"- {i}" for i in issues)
    return (
        "Ta réponse ne respecte pas les règles. Problèmes détectés par le code :\n"
        f"{lines}\n\n"
        "Renvoie la réponse complète corrigée. Pour un nombre ou une date que la personne "
        "n'a pas donné : retire-le de la proposition et DEMANDE-le dans ton message."
    )


def opening_message(base: ExperienceBase | None, gaps: list[Gap], empty: bool) -> str:
    """Premier message de l'intervieweur, DÉTERMINISTE (aucun appel au modèle)."""
    if empty:
        return (
            "Bonjour ! On va construire ton profil ensemble, pas à pas. Je te poserai "
            "des questions et je te proposerai au fur et à mesure des éléments à ajouter : "
            "tu restes libre de les accepter, les modifier ou les refuser.\n\n"
            "Pour commencer, parle-moi de toi : où en es-tu aujourd'hui, et qu'est-ce que "
            "tu recherches ?"
        )
    first = gaps[0] if gaps else None
    points = "\n".join(f"- {SECTION_LABELS[g.section]} : {g.message}" for g in gaps[:5])
    intro = "J'ai parcouru ton profil. "
    if not first:
        return (
            intro + "Il est déjà bien rempli ! On peut quand même l'enrichir : "
            "parle-moi d'une expérience ou d'un projet dont tu es fier ou fière ?"
        )
    return (
        intro
        + "Voici ce qu'on pourrait compléter en priorité :\n"
        + points
        + f"\n\nOn commence par « {SECTION_LABELS[first.section]} » ? "
        + "Dis-m'en un peu plus."
    )


__all__ = [
    "DEFAULT_LANGUAGE",
    "HISTORY_WINDOW",
    "SUMMARY_EVERY",
    "build_messages",
    "compact_profile",
    "correction_message",
    "opening_message",
    "state_block",
    "summary_cut",
    "summary_messages",
    "system_prompt",
]
