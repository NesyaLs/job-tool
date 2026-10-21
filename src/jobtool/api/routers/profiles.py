"""Routes /profiles : profils multiples d'un compte (réservés à l'administration).

`GET /profiles` répond à TOUT compte : le front y lit s'il doit afficher le sélecteur
(`allowed`). Les modifications renvoient 403 à un compte non autorisé. Le profil actif choisi
ici s'applique ensuite à toutes les routes (profil, génération, documents, entretien) via la
dépendance `get_active_profile`.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from jobtool.api.deps import get_profiles_service
from jobtool.core.experience_base.profiles import ProfileLimitReached, ProfileNotFound
from jobtool.services.profiles_service import (
    ProfilesForbidden,
    ProfilesOverview,
    ProfilesService,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileOut(BaseModel):
    key: str
    name: str
    principal: bool
    created_at: str | None = None


class ProfilesOut(BaseModel):
    allowed: bool
    active: str
    profiles: list[ProfileOut]


class CreateIn(BaseModel):
    name: str
    #: Clé du profil à copier ("" = principal) ; absent : profil vide.
    copy_from: str | None = None


class RenameIn(BaseModel):
    name: str


class ActivateIn(BaseModel):
    key: str


def _out(overview: ProfilesOverview) -> ProfilesOut:
    return ProfilesOut(
        allowed=overview.allowed,
        active=overview.active,
        profiles=[ProfileOut(**asdict(p)) for p in overview.profiles],
    )


def _guard(fn) -> ProfilesOut:
    try:
        return _out(fn())
    except ProfilesForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="profil introuvable")
    except ProfileLimitReached as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:  # nom vide ou trop long
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=ProfilesOut)
def list_profiles(
    service: ProfilesService = Depends(get_profiles_service),
) -> ProfilesOut:
    return _guard(service.overview)


@router.post("", response_model=ProfilesOut)
def create_profile(
    body: CreateIn, service: ProfilesService = Depends(get_profiles_service)
) -> ProfilesOut:
    return _guard(lambda: service.create(body.name, body.copy_from))


@router.patch("/{key}", response_model=ProfilesOut)
def rename_profile(
    key: str, body: RenameIn, service: ProfilesService = Depends(get_profiles_service)
) -> ProfilesOut:
    return _guard(lambda: service.rename(key, body.name))


@router.delete("/{key}", response_model=ProfilesOut)
def delete_profile(
    key: str, service: ProfilesService = Depends(get_profiles_service)
) -> ProfilesOut:
    return _guard(lambda: service.delete(key))


@router.put("/active", response_model=ProfilesOut)
def activate_profile(
    body: ActivateIn, service: ProfilesService = Depends(get_profiles_service)
) -> ProfilesOut:
    return _guard(lambda: service.activate(body.key))
