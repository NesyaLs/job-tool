"""Routes /auth/config et /auth/me : ce que le navigateur doit savoir pour se connecter,
puis pour composer ses menus.

Le front ne peut pas lire `.env`. Il a besoin de deux choses pour parler à Supabase Auth :
l'URL du projet et la clé PUBLIABLE. Cette dernière est publique par conception — elle
n'ouvre aucune donnée par elle-même, chaque lecture restant soumise à la vérification du
jeton par cette API. Ni le mot de passe de la base, ni la clé secrète ne sortent jamais.

`auth_required` dit au front s'il doit demander une connexion : sur une instance
mono-poste (pas de JWKS configuré), il n'y a pas de compte à saisir.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from jobtool.api.deps import get_admin_service, get_current_user_id, get_settings
from jobtool.api.schemas import AuthConfigOut, MeOut
from jobtool.services.admin_service import AdminService
from jobtool.settings import AggregatorSettings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigOut)
def auth_config(
    settings: AggregatorSettings = Depends(get_settings),
) -> AuthConfigOut:
    """Configuration publique d'authentification (jamais de secret)."""
    return AuthConfigOut(
        auth_required=bool(settings.supabase_jwks_url),
        supabase_url=settings.supabase_url or "",
        publishable_key=settings.supabase_publishable_key or "",
    )


@router.get("/me", response_model=MeOut)
def me(
    user_id: uuid.UUID = Depends(get_current_user_id),
    service: AdminService = Depends(get_admin_service),
) -> MeOut:
    """Droits du compte de la requête (401 sans jeton valide, comme toute route de données).

    Sert à l'interface pour n'afficher Administration et Journal LLM qu'aux administrateurs ;
    la protection reste celle des routes /admin/* (`require_admin`, 403).
    """
    return MeOut(is_admin=service.is_admin(user_id))
