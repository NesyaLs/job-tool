"""Assemblage déterministe du modèle de RENDU de la lettre (AUCUN LLM ici).

À partir de la base + la décision de la passe 1 + le draft de la passe 2, on remplit un
`CoverLetterRender` (consommé par cover_letter/render.py). C'est du CODE pur, mesurable et
testable sans clé : l'en-tête est de la DONNÉE PURE (jamais générée), la date est calculée,
et les blocs conditionnels sont pilotés par la présence des inserts du draft (eux-mêmes
pilotés par le mode décidé en passe 1).

Champs de l'en-tête CONSTATÉS dans core/experience_base/models.py (Profile) et data/moi.yaml :
  - nom         <- profile.name
  - coordonnees <- profile.email, profile.phone, profile.links (une entrée par ligne)
  - lieu        <- profile.location (seul champ de lieu de la base)
  - entreprise  <- routing.entreprise (extraite de l'offre en passe 1 ; PAS de poste)
  - objet       <- profile.objet s'il existe un jour (absent du modèle actuel -> None).
                   Lu via getattr pour rester forward-compatible SANS modifier core/.
"""

from __future__ import annotations

from datetime import date

from jobtool.core.experience_base.models import ExperienceBase, Profile
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRender,
    CoverLetterRoutingOutput,
)

# Mois en français, EN DUR : robuste, ne dépend d'AUCUNE locale système installée.
_MOIS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def format_date_fr(day: date) -> str:
    """Date française lisible, ex. « 15 juin 2026 » (et « 1er » pour le premier du mois)."""
    jour = "1er" if day.day == 1 else str(day.day)
    return f"{jour} {_MOIS_FR[day.month - 1]} {day.year}"


def _coordonnees(profile: Profile) -> list[str]:
    """Lignes de coordonnées de l'en-tête (une par entrée), valeurs vides ignorées."""
    bits: list[str] = []
    if profile.email:
        bits.append(profile.email)
    if profile.phone:
        bits.append(profile.phone)
    for link in profile.links or []:
        if link:
            bits.append(link)
    return bits


def build_cover_letter_render(
    base: ExperienceBase,
    routing: CoverLetterRoutingOutput,
    draft: CoverLetterDraftOutput,
    today: date | None = None,
) -> CoverLetterRender:
    """Assemble le CoverLetterRender final (en-tête pur + corps du draft + blocs conditionnels).

    `today` injectable pour des tests/preuves reproductibles ; par défaut = date du jour.
    Les blocs conditionnels suivent la présence des inserts du draft : show_* = insert non None.
    """
    profile = base.profile
    today = today or date.today()
    # objet : absent du modèle Profile actuel -> None. getattr garde la porte ouverte si le
    # champ est ajouté plus tard à core/, SANS modifier core ici. Vide -> None (template gère).
    objet = getattr(profile, "objet", None) or None

    return CoverLetterRender(
        nom=profile.name,
        coordonnees=_coordonnees(profile),
        date=format_date_fr(today),
        lieu=profile.location or "",
        entreprise=routing.entreprise,
        objet=objet,
        paragraphe_vous=draft.paragraphe_vous,
        paragraphe_moi=draft.paragraphe_moi,
        paragraphe_nous=draft.paragraphe_nous,
        formule=draft.formule,
        # La présence de l'insert (pilotée par le mode en passe 1/2) décide de l'affichage.
        show_interet=draft.insert_interet is not None,
        insert_interet=draft.insert_interet or "",
        show_projet=draft.insert_projet is not None,
        insert_projet=draft.insert_projet or "",
    )
