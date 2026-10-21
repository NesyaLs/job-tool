"""Source de rendu d'un CV réécrite à la main (mode pro) : forme attendue et complétion.

En mode normal, `render_input` est assemblé par cv/render.py : sa forme est garantie. En
mode pro, c'est l'utilisateur qui l'écrit. Ce modèle décrit ce que les templates lisent
(`cv_*.typ`) pour deux raisons :
  - REFUSER une source inexploitable avec un message qui pointe l'endroit fautif
    (« experiences.0.bullets.1.text »), au lieu d'une erreur de compilation Typst ;
  - COMPLÉTER ce qui manque (listes vides, textes vides, options par défaut), pour qu'une
    clé retirée par erreur ne fasse pas échouer le template sur `data.profile.links`.

Les clés inconnues sont conservées telles quelles : un template peut en lire d'autres.
Aucune borne de contenu ici (budget, traçabilité) : en mode pro, l'utilisateur assume le
contenu ; seules la forme et la compilation sont vérifiées.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobtool.documents.cv.profiles import OptionValue


class _Source(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class ProfileSource(_Source):
    name: str = ""
    contact: str = ""  # ligne de contact composée (classic, compact)
    links: list[str] = Field(default_factory=list)
    # Champs séparés (basic) : sans schéma http(s)://, le template l'ajoute.
    email: str = ""
    phone: str = ""
    location: str = ""
    github: str = ""
    linkedin: str = ""
    site: str = ""


class EducationSource(_Source):
    degree: str = ""
    school: str = ""
    dates: str = ""


class LanguageSource(_Source):
    name: str = ""
    level: str = ""


class BulletSource(_Source):
    id: str = ""  # id de traçabilité ; libre en mode pro
    text: str = ""


class ExperienceSource(_Source):
    # Décide de la SECTION : Expérience (internship, job) ou Projets (les deux autres).
    type: Literal["internship", "job", "school_project", "personal_project"] = "job"
    id: str = ""
    title: str = ""
    organization: str = ""
    dates: str = ""
    bullets: list[BulletSource] = Field(default_factory=list)


class CvRenderSource(_Source):
    """Tout ce que lisent les templates de CV, avec des valeurs par défaut neutres."""

    profile: ProfileSource = Field(default_factory=ProfileSource)
    education: list[EducationSource] = Field(default_factory=list)
    languages: list[LanguageSource] = Field(default_factory=list)
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    experiences: list[ExperienceSource] = Field(default_factory=list)
    show_soft_skills: bool = False
    show_accroche: bool = False
    accroche: str = ""
    options: dict[str, OptionValue] = Field(default_factory=dict)


def normalize_cv_source(data: dict) -> dict:
    """Source validée et complétée. Lève pydantic.ValidationError si inexploitable."""
    return CvRenderSource.model_validate(data).model_dump()
