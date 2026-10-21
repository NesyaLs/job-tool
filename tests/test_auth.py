"""Authentification : vérification RÉELLE de la signature, et rattachement des comptes.

Aucun bouchon de vérification : on génère une paire de clés ES256, on sert le JWKS en HTTP
sur un port local, et on signe de vrais jetons. Ce qui est prouvé est donc ce qui protégera
la production — un test qui bouchonnerait `jwt.decode` ne prouverait rien du tout.

Les cas couverts sont ceux par lesquels on entre par effraction :
  - aucun jeton ; jeton signé par une AUTRE clé ; jeton expiré ; mauvais émetteur.
Plus la mécanique de comptes : création à la première connexion, rattachement par e-mail
vérifié, et refus quand l'e-mail appartient déjà à une autre identité.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from jobtool.api.auth import authenticate, verify_token
from jobtool.core.db import (
    LOCAL_USER_ID,
    engine_for_sqlite_file,
    ensure_user,
    resolve_account,
    users,
)
from jobtool.settings import AggregatorSettings

PROJECT_URL = "https://projet-de-test.supabase.co"
ISSUER = PROJECT_URL + "/auth/v1"
KID = "cle-de-test"


@pytest.fixture(scope="module")
def keypair():
    """Paire ES256 : la privée signe les jetons du test, la publique va dans le JWKS."""
    private = ec.generate_private_key(ec.SECP256R1())
    return private, private.public_key()


@pytest.fixture
def jwks_url(keypair):
    """JWKS servi en HTTP sur un port libre, comme le ferait Supabase.

    Un fichier `file://` serait plus simple, mais PyJWKClient le REFUSE — à raison : une
    URL de clés doit venir du réseau, pas du disque de la machine. On sert donc vraiment
    du HTTP, ce qui exerce au passage la récupération et la mise en cache des clés.
    """
    _, public = keypair
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
    jwk.update({"kid": KID, "use": "sig", "alg": "ES256"})
    payload = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):  # silence : le test n'est pas un serveur web
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/jwks.json"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def settings(jwks_url):
    """Settings d'une instance AVEC authentification (sans lire le .env du dépôt)."""
    return AggregatorSettings(
        _env_file=None,
        supabase_url=PROJECT_URL,
        supabase_jwks_url=jwks_url,
    )


@pytest.fixture
def engine(tmp_path):
    """Base jetable, schéma créé."""
    from jobtool.core.db import create_all

    eng = engine_for_sqlite_file(tmp_path / "app.db")
    with eng.connect() as conn:
        create_all(conn)
        conn.commit()
    return eng


def make_token(
    keypair,
    *,
    sub: str = "sup-abc-123",
    email: str = "alice@exemple.fr",
    issuer: str = ISSUER,
    audience: str = "authenticated",
    expires_in: int = 3600,
    key=None,
) -> str:
    private, _ = keypair
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "email": email,
            "aud": audience,
            "iss": issuer,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "user_metadata": {"full_name": "Alice Exemple"},
        },
        key or private,
        algorithm="ES256",
        headers={"kid": KID},
    )


# --- Vérification du jeton ---------------------------------------------------


def test_valid_token_yields_the_identity(keypair, settings):
    user = verify_token(make_token(keypair), settings)
    assert user.auth_id == "sup-abc-123"
    assert user.email == "alice@exemple.fr"
    assert user.name == "Alice Exemple"


def test_token_signed_by_another_key_is_refused(keypair, settings):
    """Le cas qui compte : une signature valide… mais pas par NOTRE clé."""
    intruder = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(HTTPException) as err:
        verify_token(make_token(keypair, key=intruder), settings)
    assert err.value.status_code == 401


def test_expired_token_is_refused(keypair, settings):
    with pytest.raises(HTTPException) as err:
        verify_token(make_token(keypair, expires_in=-10), settings)
    assert err.value.status_code == 401


def test_token_from_another_project_is_refused(keypair, settings):
    """Un jeton signé par un AUTRE projet Supabase ne doit pas ouvrir le nôtre."""
    with pytest.raises(HTTPException) as err:
        verify_token(
            make_token(keypair, issuer="https://autre-projet.supabase.co/auth/v1"),
            settings,
        )
    assert err.value.status_code == 401


def test_wrong_audience_is_refused(keypair, settings):
    with pytest.raises(HTTPException) as err:
        verify_token(make_token(keypair, audience="anon"), settings)
    assert err.value.status_code == 401


# --- Porte d'entrée ----------------------------------------------------------


def test_no_token_is_refused_when_auth_is_configured(settings, engine):
    with pytest.raises(HTTPException) as err:
        authenticate(engine, settings, None)
    assert err.value.status_code == 401
    assert err.value.headers.get("WWW-Authenticate") == "Bearer"


def test_malformed_authorization_header_is_refused(settings, engine, keypair):
    for header in ("", "Basic abc", make_token(keypair), "Bearer "):
        with pytest.raises(HTTPException) as err:
            authenticate(engine, settings, header)
        assert err.value.status_code == 401


def test_without_auth_configured_the_local_account_is_used(engine):
    """Mode mono-poste : pas de JWKS, pas de jeton, le compte local — et rien d'autre."""
    offline = AggregatorSettings(_env_file=None, supabase_jwks_url=None)
    assert authenticate(engine, offline, None) == LOCAL_USER_ID


# --- Comptes : création, rattachement, conflit -------------------------------


def test_first_login_creates_the_account(engine, settings, keypair):
    user_id = authenticate(engine, settings, "Bearer " + make_token(keypair))
    with engine.connect() as conn:
        row = conn.execute(users.select().where(users.c.id == user_id)).one()
    assert row.email == "alice@exemple.fr"
    assert row.auth_id == "sup-abc-123"
    assert row.is_admin is False, "l'administration ne s'auto-attribue pas"


def test_second_login_reuses_the_same_account(engine, settings, keypair):
    header = "Bearer " + make_token(keypair)
    first = authenticate(engine, settings, header)
    second = authenticate(engine, settings, header)
    assert first == second


def test_existing_account_is_claimed_by_verified_email(engine, settings, keypair):
    """Le compte issu de la reprise des données retrouve son propriétaire à la connexion.

    C'est le scénario réel : les offres et documents ont été importés sous un compte créé
    AVANT toute authentification. Il porte l'e-mail, pas encore l'identité du fournisseur.
    """
    existing = uuid.uuid4()
    with engine.connect() as conn:
        ensure_user(conn, existing, email="alice@exemple.fr", name="Compte importé")
        conn.commit()

    user_id = authenticate(engine, settings, "Bearer " + make_token(keypair))

    assert user_id == existing, "aucune donnée orpheline : c'est le MÊME compte"
    with engine.connect() as conn:
        row = conn.execute(users.select().where(users.c.id == existing)).one()
    assert row.auth_id == "sup-abc-123", (
        "l'identité du fournisseur y est désormais liée"
    )


def test_email_already_linked_to_another_identity_is_refused(engine, settings, keypair):
    """Deux identités ne se disputent pas un e-mail : on refuse au lieu de deviner."""
    with engine.connect() as conn:
        resolve_account(conn, "sup-premier", "alice@exemple.fr")

    with pytest.raises(HTTPException) as err:
        authenticate(engine, settings, "Bearer " + make_token(keypair))
    assert err.value.status_code == 409
