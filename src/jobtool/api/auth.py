"""Authentification : vérification des jetons émis par Supabase Auth.

Le navigateur s'authentifie DIRECTEMENT auprès de Supabase (mot de passe, e-mail vérifié,
réinitialisation — tout ce qu'on ne doit pas réécrire soi-même) et reçoit un JWT. Cette
API ne voit jamais de mot de passe : elle reçoit ce jeton et en VÉRIFIE LA SIGNATURE.

Signature ASYMÉTRIQUE (ES256) : la clé publique du projet est publiée sur son JWKS, la clé
privée ne quitte jamais Supabase. Aucun secret partagé n'a donc à vivre dans ce dépôt —
c'est la raison pour laquelle on préfère le JWKS au « JWT secret » historique.

Ce qu'on vérifie, et pourquoi chaque contrôle compte :
  - la SIGNATURE, contre la clé publique désignée par le `kid` de l'en-tête ;
  - l'EXPIRATION, sinon un jeton volé vaudrait éternellement ;
  - l'ÉMETTEUR (`iss`), sinon un jeton signé par un AUTRE projet Supabase serait accepté ;
  - l'AUDIENCE (`aud`), pour n'accepter que des jetons d'utilisateur connecté.

Deux modes, comme pour la base — un seul interrupteur :
  - `supabase_jwks_url` absent  -> mode MONO-POSTE : pas d'authentification, le compte
    local. C'est le mode de la suite de tests et d'un usage sur son propre ordinateur.
  - présent -> tout appel doit porter un jeton valide.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import Engine

from jobtool.core.db import LOCAL_USER_ID, AccountConflict, resolve_account
from jobtool.settings import AggregatorSettings

#: Audience des jetons d'utilisateur connecté chez Supabase.
_AUDIENCE = "authenticated"


@dataclass(frozen=True)
class AuthenticatedUser:
    """Ce qu'on retient d'un jeton vérifié : rien de plus que l'identité."""

    auth_id: str  # `sub` du jeton = auth.users.id chez Supabase
    email: str
    name: str = ""


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    """Client JWKS mis en cache : il garde les clés publiques et ne les retélécharge
    qu'au renouvellement (un aller-retour réseau par requête serait absurde)."""
    return PyJWKClient(jwks_url, cache_keys=True)


def _issuer(settings: AggregatorSettings) -> str | None:
    """Émetteur attendu : `<projet>/auth/v1`. None si l'URL du projet n'est pas configurée."""
    if not settings.supabase_url:
        return None
    return settings.supabase_url.rstrip("/") + "/auth/v1"


def verify_token(token: str, settings: AggregatorSettings) -> AuthenticatedUser:
    """Vérifie un jeton et en extrait l'identité. Lève HTTPException(401) sinon.

    401 et non 403 : c'est l'AUTHENTIFICATION qui échoue (jeton absent, expiré, invalide),
    pas une autorisation qui manque. Le message reste générique — dire *pourquoi* un jeton
    est refusé renseigne autant l'attaquant que l'utilisateur.
    """
    jwks_url = settings.supabase_jwks_url
    if not jwks_url:  # ne devrait pas arriver : l'appelant teste le mode avant
        raise HTTPException(status_code=500, detail="authentification non configurée")

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience=_AUDIENCE,
            issuer=_issuer(settings),
            options={"require": ["exp", "sub"]},
        )
    except Exception as exc:  # signature, expiration, émetteur, jeton illisible…
        raise HTTPException(status_code=401, detail="jeton invalide ou expiré") from exc

    email = claims.get("email") or ""
    metadata = claims.get("user_metadata") or {}
    return AuthenticatedUser(
        auth_id=str(claims["sub"]),
        email=email,
        name=str(metadata.get("full_name") or metadata.get("name") or ""),
    )


def authenticate(
    engine: Engine | None,
    settings: AggregatorSettings,
    authorization: str | None,
) -> uuid.UUID:
    """Identifiant INTERNE du compte appelant.

    Mono-poste (pas de JWKS configuré) : le compte local, sans jeton.
    Sinon : jeton obligatoire, vérifié, puis traduit en compte interne (créé ou rattaché).
    """
    if not settings.supabase_jwks_url:
        return LOCAL_USER_ID

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="authentification requise",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = verify_token(token, settings)
    if engine is None:  # authentification configurée sans base : incohérent, on le dit
        raise HTTPException(status_code=500, detail="base de données non configurée")

    with engine.connect() as conn:
        try:
            user_id = resolve_account(conn, user.auth_id, user.email, user.name)
        except AccountConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return user_id


def bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    """En-tête `Authorization` brut, injecté par FastAPI (documenté dans l'OpenAPI)."""
    return authorization


__all__ = ["AuthenticatedUser", "authenticate", "bearer_token", "verify_token"]
