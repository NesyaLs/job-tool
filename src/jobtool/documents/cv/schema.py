"""Schéma de sortie du LLM : un CV adapté à une offre.

Ce module ne décrit QUE la forme attendue de ce que le LLM produira plus tard.
La garantie anti-fabrication (chaque id pointe vers une entrée réelle de la base)
n'est pas portée ici mais dans traceability.check_traceability.

Sous-schémas internes pour la génération en deux appels :
  - TailoredExperiencesOutput : sortie de l'appel A (expériences + bullets).
  - TailoredSkillsOutput      : sortie de l'appel B (listes ordonnées de compétences).
Le TailoredCV final est assemblé depuis les deux ; sa forme ne change pas.
"""

from __future__ import annotations

from pydantic import BaseModel


class TailoredBullet(BaseModel):
    """Une puce reformulée, qui doit tracer vers un achievement réel."""

    source_id: str  # doit correspondre à un achievement.id existant dans la base
    text: str  # version reformulée de l'achievement, adaptée à l'offre


class TailoredExperience(BaseModel):
    """Une expérience du CV adapté, traçant vers une expérience réelle."""

    source_experience_id: str  # doit correspondre à une experience.id de la base
    bullets: list[TailoredBullet]  # sous-ensemble sélectionné/réordonné/reformulé


class TailoredExperiencesOutput(BaseModel):
    """Sortie de l'appel A : uniquement la liste des expériences sélectionnées."""

    experiences: list[TailoredExperience]


class TailoredSkillsOutput(BaseModel):
    """Sortie de l'appel B : compétences et intérêts ordonnés par pertinence décroissante.

    Chaque élément DOIT être repris à l'identique depuis les listes maîtres de la base
    (vérifié par check_traceability). Aucune invention, reformulation ou raccourcissement.

    `focus` : court groupe nominal à insérer dans la phrase d'accroche adaptable. Rempli
    UNIQUEMENT quand le prompt B le demande (accroche adaptable + show_accroche actif) ;
    vide sinon. Non soumis à la traçabilité (choix assumé : accroche opt-in, relue par l'humain).
    """

    hard_skills: list[str] = []
    soft_skills: list[str] = []
    interests: list[str] = []
    focus: str = ""


class TailoredCV(BaseModel):
    """Racine du CV produit par le LLM.

      - experiences : zone TRACÉE (chaque bullet pointe vers un achievement réel).
      - soft_skills / hard_skills / interests : SÉLECTIONS ordonnées des listes
        maîtres de la base, reprises à l'identique (aucune invention/reformulation ;
        vérifié par check_traceability). Les langues ne sont PAS ici (zone en dur).
    L'identité et la formation ne sont PAS ici : elles viennent de la base au rendu.
    """

    experiences: list[TailoredExperience]
    soft_skills: list[str] = []
    hard_skills: list[str] = []
    interests: list[str] = []
    # focus : texte choisi par l'appel B pour remplir le trou {focus} d'une accroche
    # adaptable (vide si accroche fixe ou désactivée). N'altère pas le reste du schéma.
    focus: str = ""
