"""Clés d'API propres à un compte : chiffrées en base, jamais renvoyées, prioritaires.

Aucun réseau : le client Mistral et les connecteurs sont remplacés. Ce qui est vérifié,
c'est ce qui est ÉCRIT (la base ne contient aucune clé en clair) et ce qui est UTILISÉ
(la clé du compte arrive jusqu'au client du modèle et jusqu'aux sources d'offres).
"""

from __future__ import annotations

import stat
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from jobtool.api import deps
from jobtool.api.main import app
from jobtool.core.api_keys import (
    ApiKeyStore,
    SecretKeyMissing,
    load_cipher,
    with_account_keys,
)
from jobtool.core.db import LOCAL_USER_ID, connect_sqlite, ensure_user
from jobtool.settings import AggregatorSettings

SECRET = "sk-compte-0123456789"


def _settings(tmp_path, secret_key: str = "", **values) -> AggregatorSettings:
    # Champs à alias (JOBTOOL_…) : ils se construisent sous le nom de leur variable.
    aliased = {
        "JOBTOOL_SECRET_KEY_FILE": tmp_path / ".secret_key",
        "JOBTOOL_SECRET_KEY": secret_key,
    }
    return AggregatorSettings(**{"database_url": None, **aliased, **values})


@pytest.fixture
def stores(tmp_path):
    """(dépôt d'un compte, dépôt d'un AUTRE compte, chemin de la base) sur SQLite jetable."""
    db = str(tmp_path / "keys.db")
    mine, other = uuid.uuid4(), uuid.uuid4()
    with connect_sqlite(db) as conn:
        for user_id in (mine, other):
            ensure_user(conn, user_id, email=f"{user_id}@test")
        conn.commit()
    settings = _settings(tmp_path)
    return ApiKeyStore(db, mine, settings), ApiKeyStore(db, other, settings), db


# --- Stockage ---------------------------------------------------------------


def test_a_saved_key_is_encrypted_at_rest_and_read_back(stores, tmp_path):
    mine, _, db = stores
    mine.save("mistral_api_key", SECRET)

    assert mine.load() == {"mistral_api_key": SECRET}
    assert SECRET.encode() not in (tmp_path / "keys.db").read_bytes()


def test_keys_are_partitioned_by_account(stores):
    mine, other, _ = stores
    mine.save("adzuna_app_key", SECRET)

    assert other.load() == {}
    assert other.names() == set()
    assert mine.delete("adzuna_app_key") is True
    assert mine.load() == {}


def test_a_key_saved_twice_is_replaced(stores):
    mine, _, _ = stores
    mine.save("mistral_api_key", "ancienne")
    mine.save("mistral_api_key", SECRET)
    assert mine.load() == {"mistral_api_key": SECRET}


def test_unknown_key_names_are_refused(stores):
    mine, _, _ = stores
    with pytest.raises(ValueError):
        mine.save("database_url", "postgresql://pirate")


def test_local_cipher_file_is_private(tmp_path):
    load_cipher(_settings(tmp_path))
    mode = stat.S_IMODE((tmp_path / ".secret_key").stat().st_mode)
    assert mode == 0o600


def test_a_changed_encryption_key_makes_saved_keys_absent(stores, tmp_path):
    mine, _, db = stores
    mine.save("mistral_api_key", SECRET)
    other_cipher = _settings(tmp_path, secret_key=Fernet.generate_key().decode())
    assert ApiKeyStore(db, mine._user_id, other_cipher).load() == {}


def test_multi_account_mode_requires_an_explicit_secret_key(tmp_path):
    settings = _settings(tmp_path, database_url="postgresql://exemple")
    with pytest.raises(SecretKeyMissing):
        load_cipher(settings)


def test_account_keys_override_server_keys_only_when_given():
    server = AggregatorSettings(mistral_api_key="serveur", adzuna_app_id="serveur-id")
    merged = with_account_keys(server, {"mistral_api_key": "compte"})
    assert merged.mistral_api_key == "compte"
    assert merged.adzuna_app_id == "serveur-id"


def test_make_client_prefers_the_account_key(monkeypatch):
    from jobtool.core.llm import client as llm

    seen = []
    monkeypatch.setattr(llm, "Mistral", lambda api_key: seen.append(api_key))
    monkeypatch.setenv("MISTRAL_API_KEY", "serveur")

    llm.make_client()
    with llm.using_api_key("compte"):
        llm.make_client()
    with llm.using_api_key(None):
        llm.make_client()
    assert seen == ["serveur", "compte", "serveur"]


# --- API --------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    """API sur une base jetable, en mono-poste, avec une clé Mistral côté serveur."""
    db_file = str(tmp_path / "api.db")
    settings = _settings(tmp_path, mistral_api_key="serveur")
    app.dependency_overrides[deps.get_offers_db] = lambda: db_file
    app.dependency_overrides[deps.get_settings] = lambda: settings
    yield TestClient(app), db_file
    app.dependency_overrides.clear()


def _status(client) -> dict:
    body = client.get("/settings/api-keys").json()
    return {"can_store": body["can_store"], **{k["name"]: k for k in body["keys"]}}


def test_api_never_returns_a_key_value(api):
    client, _ = api
    assert (
        client.put(
            "/settings/api-keys/mistral_api_key", json={"value": SECRET}
        ).status_code
        == 204
    )

    response = client.get("/settings/api-keys")
    assert SECRET not in response.text
    status = _status(client)
    assert status["can_store"] is True
    assert status["mistral_api_key"]["account"] is True
    assert status["mistral_api_key"]["server"] is True
    assert status["adzuna_app_key"] == {
        "name": "adzuna_app_key",
        "label": "Adzuna — app_key",
        "account": False,
        "server": False,
    }


def test_api_saves_trimmed_keys_and_deletes_them(api):
    client, db_file = api
    client.put("/settings/api-keys/adzuna_app_key", json={"value": f"  {SECRET}\n"})
    settings = app.dependency_overrides[deps.get_settings]()
    assert ApiKeyStore(db_file, LOCAL_USER_ID, settings).load() == {
        "adzuna_app_key": SECRET
    }

    assert client.delete("/settings/api-keys/adzuna_app_key").status_code == 204
    assert _status(client)["adzuna_app_key"]["account"] is False


def test_api_rejects_unknown_names_and_empty_values(api):
    client, _ = api
    assert (
        client.put("/settings/api-keys/database_url", json={"value": "x"}).status_code
        == 404
    )
    assert (
        client.put(
            "/settings/api-keys/mistral_api_key", json={"value": "   "}
        ).status_code
        == 422
    )
    assert (
        client.put("/settings/api-keys/mistral_api_key", json={"value": ""}).status_code
        == 422
    )


def test_api_refuses_to_store_without_a_secret_key_in_multi_account_mode(api, tmp_path):
    client, _ = api
    remote = _settings(tmp_path, database_url="postgresql://exemple")
    app.dependency_overrides[deps.get_settings] = lambda: remote

    assert _status(client)["can_store"] is False
    response = client.put("/settings/api-keys/mistral_api_key", json={"value": SECRET})
    assert response.status_code == 503
    assert "JOBTOOL_SECRET_KEY" in response.json()["detail"]


def test_the_account_key_reaches_the_model_client(api):
    client, _ = api
    client.put("/settings/api-keys/mistral_api_key", json={"value": SECRET})

    db_file = app.dependency_overrides[deps.get_offers_db]()
    server = app.dependency_overrides[deps.get_settings]()
    store = deps.get_api_key_store(LOCAL_USER_ID, db_file, server)
    assert deps.get_account_settings(store, server).mistral_api_key == SECRET
    factory = deps.get_llm_client_factory(deps.get_account_settings(store, server))
    from jobtool.core.llm import client as llm

    seen = []
    original = llm.Mistral
    llm.Mistral = lambda api_key: seen.append(api_key)
    try:
        factory()
    finally:
        llm.Mistral = original
    assert seen == [SECRET]


def test_a_collection_uses_the_keys_of_the_account_that_starts_it(api, monkeypatch):
    from jobtool.services import aggregation_service

    client, _ = api
    client.put("/settings/api-keys/adzuna_app_id", json={"value": "id-du-compte"})
    client.put("/settings/api-keys/adzuna_app_key", json={"value": SECRET})
    seen = []
    monkeypatch.setattr(
        aggregation_service,
        "build_connectors",
        lambda names, ctx: (seen.append(ctx.settings) or [], []),
    )

    assert client.post("/aggregate").status_code == 202
    assert seen and seen[0].adzuna_app_id == "id-du-compte"
    assert seen[0].adzuna_app_key == SECRET
