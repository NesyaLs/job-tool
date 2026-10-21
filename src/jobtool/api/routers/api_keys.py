"""Routes /settings/api-keys : les clés d'API PROPRES au compte (Mistral, sources d'offres).

  GET    /settings/api-keys          -> pour chaque clé : saisie par le compte ? fournie par
                                        le serveur ? JAMAIS la valeur.
  PUT    /settings/api-keys/{name}   -> enregistre (chiffrée) la clé du compte. 204.
  DELETE /settings/api-keys/{name}   -> la retire : le serveur reprend la main. 204.

Une clé du compte l'emporte sur celle du serveur, pour ce compte seulement (voir
core/api_keys.py et `get_account_settings`). Une valeur saisie ne ressort jamais de l'API :
pour la changer, on la ressaisit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from jobtool.api.deps import get_api_key_store, get_settings
from jobtool.api.schemas import ApiKeyIn, ApiKeysOut, ApiKeyStatus
from jobtool.core.api_keys import API_KEY_NAMES, ApiKeyStore, SecretKeyMissing
from jobtool.settings import AggregatorSettings

router = APIRouter(prefix="/settings/api-keys", tags=["settings"])

LABELS = {
    "mistral_api_key": "Mistral — clé d'API",
    "france_travail_client_id": "France Travail — identifiant client",
    "france_travail_client_secret": "France Travail — clé secrète",
    "adzuna_app_id": "Adzuna — app_id",
    "adzuna_app_key": "Adzuna — app_key",
}


def _known(name: str) -> str:
    if name not in API_KEY_NAMES:
        raise HTTPException(status_code=404, detail=f"clé inconnue : {name}")
    return name


@router.get("", response_model=ApiKeysOut)
def list_keys(
    store: ApiKeyStore = Depends(get_api_key_store),
    settings: AggregatorSettings = Depends(get_settings),
) -> ApiKeysOut:
    """D'où vient chaque clé pour ce compte (sans jamais la renvoyer)."""
    saved = store.names()
    return ApiKeysOut(
        can_store=bool(settings.secret_key) or not settings.database_url,
        keys=[
            ApiKeyStatus(
                name=name,
                label=LABELS[name],
                account=name in saved,
                server=bool(getattr(settings, name)),
            )
            for name in API_KEY_NAMES
        ],
    )


@router.put("/{name}", status_code=204)
def save_key(
    body: ApiKeyIn,
    name: str = Depends(_known),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> Response:
    """Enregistre la clé du compte (chiffrée). Les espaces autour sont retirés."""
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="clé vide")
    try:
        store.save(name, value)
    except SecretKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(status_code=204)


@router.delete("/{name}", status_code=204)
def delete_key(
    name: str = Depends(_known),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> Response:
    """Retire la clé du compte ; celle du serveur, s'il en a une, reprend la main."""
    store.delete(name)
    return Response(status_code=204)
