"""Contrôles d'un document RETOUCHÉ à la main, rejoués sur le texte réellement rendu.

Le principe du projet ne change pas d'un pouce ici : le LLM (ou l'humain) propose, le CODE
mesure. Une retouche manuelle ne doit donc pas être un angle mort — elle repasse par les
MÊMES bornes dures que la génération :

  - une page          -> check_single_page   (mesure sur le PDF final)
  - texte extractible -> check_pdf_ats       (cibles recalculées sur le texte ÉDITÉ)
  - budget du template-> check_budget        (longueurs/comptages, profil du template)
  - traçabilité       -> check_traceability  (les ids, eux, n'ont pas bougé)

⚠ Ce que le code NE peut PAS garantir après une retouche : l'anti-fabrication. La
traçabilité vérifie qu'une puce POINTE vers un achievement réel, pas que son texte en soit
une reformulation fidèle. Un humain qui réécrit une puce sort donc, volontairement, de la
garantie « chaque fait est tracé ». C'est un choix assumé (c'est tout l'intérêt de la
retouche), à condition qu'il reste VISIBLE : `edited_field_ids` est remonté jusqu'à l'écran
d'édition, qui signale les champs concernés.
"""

from __future__ import annotations

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.traceability import Violation, check_traceability
from jobtool.documents.cv.ats_targets import bullet_fragment
from jobtool.documents.cv.budget import check_budget
from jobtool.documents.cv.profiles import TemplateProfile
from jobtool.documents.cv.schema import TailoredBullet, TailoredCV, TailoredExperience
from jobtool.documents.drafts.kinds import RenderInput
from jobtool.documents.drafts.models import DocumentKind


def rebuild_tailored_cv(render_input: RenderInput) -> TailoredCV:
    """Reconstruit le TailoredCV correspondant à un `render_input` de CV.

    Rendu possible par les ids conservés dans le render_input (id d'expérience + id
    d'achievement par puce). Permet de rejouer check_budget / check_traceability sur
    l'état ÉDITÉ sans dupliquer une seule de leurs règles.
    """
    experiences = [
        TailoredExperience(
            source_experience_id=experience.get("id", ""),
            bullets=[
                TailoredBullet(source_id=bullet["id"], text=bullet["text"])
                for bullet in experience.get("bullets", [])
            ],
        )
        for experience in render_input.get("experiences", [])
    ]
    return TailoredCV(
        experiences=experiences,
        hard_skills=list(render_input.get("hard_skills", [])),
        soft_skills=list(render_input.get("soft_skills", [])),
        interests=list(render_input.get("interests", [])),
        focus=str(render_input.get("focus", "")),
    )


def cv_ats_targets(render_input: RenderInput) -> list[str]:
    """Cibles ATS d'un CV : le nom, puis un fragment de chaque puce non vide."""
    targets = [render_input["profile"]["name"]]
    targets.extend(
        bullet_fragment(bullet["text"])
        for experience in render_input.get("experiences", [])
        for bullet in experience.get("bullets", [])
        if bullet["text"].strip()
    )
    return targets


def letter_ats_targets(render_input: RenderInput) -> list[str]:
    """Cibles ATS d'une lettre : les mêmes que le pipeline de génération."""
    return [render_input["nom"], render_input["entreprise"], render_input["date"]]


def ats_targets(kind: DocumentKind, render_input: RenderInput) -> list[str]:
    """Chaînes qui doivent rester extractibles du PDF, calculées sur le texte ÉDITÉ.

    On ne réutilise pas les cibles d'origine : après retouche elles ne décrivent plus le
    document. Le contrôle ATS doit porter sur ce qui est réellement écrit maintenant.
    """
    from jobtool.documents.drafts.kinds import policy_for

    return policy_for(kind).ats_targets(render_input)


def cv_content_issues(
    render_input: RenderInput,
    base: ExperienceBase | None,
    profile: TemplateProfile | None,
) -> tuple[list[Violation], list[str]]:
    """(violations de traçabilité, dépassements de budget) de l'état édité d'un CV.

    Sans base ni profil disponibles, on ne rapporte rien plutôt que d'inventer un verdict.
    """
    if base is None or profile is None:
        return [], []
    cv = rebuild_tailored_cv(render_input)
    return list(check_traceability(base, cv)), list(check_budget(base, cv, profile))


def no_content_issues(
    _render_input: RenderInput,
    _base: ExperienceBase | None,
    _profile: TemplateProfile | None,
) -> tuple[list[Violation], list[str]]:
    """Aucune borne de contenu — c'est le cas de la lettre.

    Elle n'a ni budget de template ni structure tracée par ids côté rendu : ses garde-fous
    vivent dans les passes de génération, pas dans la retouche.
    """
    return [], []


def content_issues(
    kind: DocumentKind,
    render_input: RenderInput,
    base: ExperienceBase | None,
    profile: TemplateProfile | None,
) -> tuple[list[Violation], list[str]]:
    """Bornes de CONTENU applicables à ce type de document, sur l'état édité."""
    from jobtool.documents.drafts.kinds import policy_for

    return policy_for(kind).content_issues(render_input, base, profile)
