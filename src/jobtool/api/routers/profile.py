"""Routes /profile : base d'expériences (GET/PUT) et photo de profil.

Contrat identique au reste de l'API : recevoir -> appeler le service -> renvoyer du JSON.
Aucune logique métier ni accès YAML ici ; tout passe par `ProfileService`. Erreurs : base
introuvable -> 404 ; profil invalide (dates, ids en double…) -> 400 avec message lisible.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from jobtool.api.deps import get_bullet_suggestion_service, get_profile_service
from jobtool.core.experience_base.photo import MAX_INPUT_BYTES, PhotoError
from jobtool.core.experience_base.store import ExperienceBaseMissing
from jobtool.interview.bullets import TargetExperience
from jobtool.services.bullet_suggestion_service import (
    BulletSuggestionService,
    SuggestionError,
)
from jobtool.services.profile_service import (
    ProfileDTO,
    ProfileError,
    ProfileExperienceDTO,
    ProfileService,
)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileDTO)
def get_profile(
    service: ProfileService = Depends(get_profile_service),
) -> ProfileDTO:
    """Renvoie le profil (base d'expériences projetée à plat)."""
    try:
        return service.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="base d'expériences introuvable")
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("", response_model=ProfileDTO)
def put_profile(
    body: ProfileDTO,
    service: ProfileService = Depends(get_profile_service),
) -> ProfileDTO:
    """Enregistre le profil (champs non édités préservés) et renvoie le profil relu."""
    try:
        return service.write(body)
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- Propositions de réalisations -------------------------------------------


class SuggestionsIn(BaseModel):
    """L'expérience telle qu'elle est à l'écran (peut-être pas encore enregistrée), et la
    consigne facultative de la personne."""

    experience: ProfileExperienceDTO
    instruction: str = ""


class SuggestionsOut(BaseModel):
    bullets: list[str]


@router.post("/suggestions", response_model=SuggestionsOut)
def suggest_bullets(
    body: SuggestionsIn,
    service: BulletSuggestionService = Depends(get_bullet_suggestion_service),
) -> SuggestionsOut:
    """Propose des puces pour une expérience. N'écrit rien dans le profil ; 502 si le
    modèle n'a pas pu répondre."""
    exp = body.experience
    target = TargetExperience(
        id=exp.id,
        title=exp.titre,
        organization=exp.org,
        type=exp.type,
        start=exp.debut,
        end=exp.fin,
        summary=exp.resume,
        bullets=[r.text for r in exp.realisations if r.text.strip()],
    )
    try:
        return SuggestionsOut(bullets=service.suggest(target, body.instruction))
    except SuggestionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# --- Photo de profil --------------------------------------------------------
#
# Une route à part plutôt qu'un champ du profil : l'image pèse ~45 Kio, qu'il serait
# absurde de transporter (en base64, donc +33 %) à chaque lecture ou enregistrement du
# profil. Le profil n'annonce que sa PRÉSENCE (`a_photo`).


@router.get("/photo")
def get_profile_photo(
    service: ProfileService = Depends(get_profile_service),
) -> Response:
    """Sert la photo du compte en JPEG ; 404 s'il n'en a pas."""
    try:
        jpeg = service.photo_bytes()
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if jpeg is None:
        raise HTTPException(status_code=404, detail="aucune photo de profil")
    # `no-store` : l'image change quand l'utilisateur la remplace, et une photo n'a rien
    # à faire dans un cache partagé.
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/photo", response_model=ProfileDTO)
async def put_profile_photo(
    fichier: UploadFile,
    service: ProfileService = Depends(get_profile_service),
) -> ProfileDTO:
    """Remplace la photo du compte et renvoie le profil relu.

    L'image est validée et normalisée AVANT d'être rangée : Typst ne détecte pas un JPEG
    tronqué et le rendrait avec un aplat gris. 413 si le fichier dépasse la limite, 422
    s'il est inutilisable — deux cas que l'utilisateur peut corriger lui-même.
    """
    raw = await fichier.read()
    if len(raw) > MAX_INPUT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"fichier trop lourd (maximum {MAX_INPUT_BYTES // 1024} Kio)",
        )
    try:
        return service.set_photo(raw)
    except PhotoError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ExperienceBaseMissing:
        raise HTTPException(
            status_code=404,
            detail="renseignez votre profil avant d'y ajouter une photo",
        )
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/photo", response_model=ProfileDTO)
def delete_profile_photo(
    service: ProfileService = Depends(get_profile_service),
) -> ProfileDTO:
    """Supprime la photo du compte (idempotent) et renvoie le profil relu."""
    try:
        return service.delete_photo()
    except ExperienceBaseMissing:
        raise HTTPException(status_code=404, detail="base d'expériences introuvable")
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
