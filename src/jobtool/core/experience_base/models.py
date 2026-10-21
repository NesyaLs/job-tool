"""Modèles Pydantic de la base d'expériences.

Objectif central du projet : anti-fabrication. Chaque élément du CV généré
plus tard devra être traçable vers une entrée précise de cette base via un id
stable. Les contraintes d'unicité d'ids codées ici sont la garantie technique
de cette traçabilité.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

# "YYYY-MM" : 4 chiffres pour l'année, un mois 01..12.
_YEAR_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _check_year_month(value: str, *, allow_present: bool) -> str:
    """Valide un champ de date au format "YYYY-MM" (et "present" si autorisé)."""
    if allow_present and value == "present":
        return value
    if not _YEAR_MONTH.match(value):
        suffix = ' (ou "present")' if allow_present else ""
        raise ValueError(f'date invalide "{value}" : format attendu "YYYY-MM"{suffix}')
    return value


class ExperienceType(str, Enum):
    """Catégorie d'une expérience du parcours."""

    internship = "internship"
    school_project = "school_project"
    personal_project = "personal_project"
    job = "job"


# Types d'expérience considérés comme des PROJETS (par opposition aux stages/jobs).
# Constante partagée (budget, fill, generate, pipeline) pour éviter de redéfinir le
# tuple à plusieurs endroits. Volontairement NON dérivée de l'enum : on fige ici la
# liste exacte des types « projet » sans coupler les modules métier à l'enum.
PROJECT_TYPES = ("school_project", "personal_project")


class Profile(BaseModel):
    """Informations d'identité / de contact, en tête de la base.

    Phrase d'ACCROCHE (opt-in, affichée seulement si le template active show_accroche) :
      - accroche : texte complet, affiché TEL QUEL (jamais touché par le LLM).
      - accroche_template : template avec un unique trou {focus} ; le LLM choisit seulement
        le texte du trou (en fonction de l'offre), le reste de la phrase est figé.
    Les deux sont optionnels. Règle si les deux sont présents : accroche_template prioritaire
    quand show_accroche est actif (cf. cv/render.py). La forme dans le YAML décide fixe vs
    adaptable — PAS un réglage de template.
    """

    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    links: list[str] | None = None
    accroche: str | None = None
    # Ancien nom de la clé (`accroche_gabarit`) accepté en lecture : les bases déjà
    # écrites le portent encore. Le prochain enregistrement réécrit le nouveau nom.
    accroche_template: str | None = Field(
        default=None,
        validation_alias=AliasChoices("accroche_template", "accroche_gabarit"),
    )
    # Photo de profil : EMPREINTE SHA-256 de l'image stockée (table `profile_photos`),
    # jamais un chemin. La base est un JSON en base de données ; un chemin libre y
    # permettrait de faire lire un autre fichier au moteur de rendu.
    # Attention : ce modèle n'a pas de `model_config`, donc pydantic IGNORE en silence
    # toute clé non déclarée — un `photo:` ajouté au YAML disparaîtrait sans erreur si ce
    # champ n'existait pas.
    photo: str | None = None


class Achievement(BaseModel):
    """Une réalisation atomique rattachée à une expérience.

    `id` doit être unique sur TOUTE la base (vérifié dans ExperienceBase) ;
    convention : "{id_de_l_experience}.{n}", ex "stage-backend-2025.1".
    """

    id: str
    text: str
    skills: list[str] = []


class Experience(BaseModel):
    """Une ligne du parcours : stage, projet école/perso, ou job."""

    id: str
    type: ExperienceType
    title: str
    organization: str
    start: str
    end: str
    summary: str | None = None
    achievements: list[Achievement] = []
    # pinned : présence OBLIGATOIRE dans tout CV généré (le LLM ne peut pas l'omettre).
    # Notion distincte de `type` (qui décide seulement de la SECTION d'affichage).
    pinned: bool = False

    @field_validator("start")
    @classmethod
    def _validate_start(cls, value: str) -> str:
        return _check_year_month(value, allow_present=False)

    @field_validator("end")
    @classmethod
    def _validate_end(cls, value: str) -> str:
        return _check_year_month(value, allow_present=True)


class Education(BaseModel):
    """Une ligne de formation : INFO EN DUR (ne passe jamais par le LLM)."""

    degree: str
    school: str
    start: str
    end: str

    @field_validator("start")
    @classmethod
    def _validate_start(cls, value: str) -> str:
        return _check_year_month(value, allow_present=False)

    @field_validator("end")
    @classmethod
    def _validate_end(cls, value: str) -> str:
        return _check_year_month(value, allow_present=True)


class Language(BaseModel):
    """Une langue parlée : INFO EN DUR (recopiée telle quelle, jamais le LLM)."""

    name: str
    level: str


class DocumentPreferences(BaseModel):
    """Réglages des documents, choisis une fois dans le profil.

    - cv_template : RÉFÉRENCE canonique du template de CV (`nom?options`, cf.
      services/template_service) ; None = template par défaut du serveur ;
    - letter_template : nom canonique du template de lettre ; None = défaut ;
    - letter_example : une lettre écrite par la personne. Présente, elle sert de BASE à
      chaque lettre générée (cover_letter/passe2) au lieu d'une simple référence de ton.
    """

    cv_template: str | None = None
    letter_template: str | None = None
    letter_example: str | None = None


class ExperienceBase(BaseModel):
    """Racine du document : identité + formation + langues + expériences + listes maîtres.

    Zones EN DUR (jamais le LLM) : profile, education, languages.
    hard_skills, soft_skills et interests sont des LISTES MAÎTRES : le LLM y
    SÉLECTIONNE un sous-ensemble à l'identique (vérifié par check_traceability),
    il n'invente rien.
    Les expériences portent les achievements traçables. Invariants d'unicité conservés.
    """

    profile: Profile
    education: list[Education] = []
    languages: list[Language] = []
    hard_skills: list[str] = []
    soft_skills: list[str] = []
    interests: list[str] = []
    experiences: list[Experience]
    documents: DocumentPreferences = DocumentPreferences()

    @model_validator(mode="after")
    def _check_no_duplicate_master_lists(self) -> ExperienceBase:
        for field_name in ("hard_skills", "soft_skills", "interests"):
            values = getattr(self, field_name)
            seen: set[str] = set()
            for value in values:
                if value in seen:
                    raise ValueError(f'doublon dans {field_name} : "{value}"')
                seen.add(value)
        return self

    @model_validator(mode="after")
    def _check_unique_ids(self) -> ExperienceBase:
        seen_experience_ids: set[str] = set()
        seen_achievement_ids: set[str] = set()

        for experience in self.experiences:
            if experience.id in seen_experience_ids:
                raise ValueError(f'id d\'expérience en double : "{experience.id}"')
            seen_experience_ids.add(experience.id)

            for achievement in experience.achievements:
                if achievement.id in seen_achievement_ids:
                    raise ValueError(
                        f'id d\'achievement en double sur la base : "{achievement.id}"'
                    )
                seen_achievement_ids.add(achievement.id)

        return self
