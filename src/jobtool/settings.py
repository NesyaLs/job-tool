"""Configuration centralisée (pydantic-settings).

Source unique des SECRETS et des chemins par défaut, lus depuis l'environnement et le
fichier `.env` (project_ctx.md : « Secrets via settings, jamais en dur, jamais loggés »). Les
connecteurs ne lisent JAMAIS l'environnement eux-mêmes : la CLI lit ces settings et injecte
les valeurs (identifiants, scope) dans les constructeurs.

Aucun secret n'est journalisé ici ; on n'expose que des attributs, jamais de `repr` enrichi.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AggregatorSettings(BaseSettings):
    """Identifiants des sources, scope OAuth, drapeau debug et chemins par défaut.

    Les identifiants absents valent `None` : la source concernée est alors ignorée /
    auto-isolée (comportement inchangé), jamais une erreur fatale. Les chemins par défaut
    sont relatifs au répertoire de lancement (racine du projet en usage normal).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Secrets / identifiants (mappés sur les mêmes variables qu'avant) ----
    # Clé du modèle : lue ici pour être disponible dans TOUT processus (API comprise),
    # et pas seulement dans les commandes CLI qui appellent load_dotenv().
    # Voir core/llm/client.make_client, qui garde la priorité à l'environnement.
    mistral_api_key: str | None = None  # MISTRAL_API_KEY
    adzuna_app_id: str | None = None  # ADZUNA_APP_ID
    adzuna_app_key: str | None = None  # ADZUNA_APP_KEY
    france_travail_client_id: str | None = None  # FRANCE_TRAVAIL_CLIENT_ID
    france_travail_client_secret: str | None = None  # FRANCE_TRAVAIL_CLIENT_SECRET
    # Clé de chiffrement des clés d'API saisies par les comptes (Fernet, cf. core/api_keys).
    # Obligatoire en multi-comptes ; en mono-poste, générée au besoin dans le fichier.
    secret_key: str | None = Field(default=None, validation_alias="JOBTOOL_SECRET_KEY")
    secret_key_path: Path = Field(
        default=Path("data/.secret_key"), validation_alias="JOBTOOL_SECRET_KEY_FILE"
    )
    # Le scope peut varier selon l'application -> configurable, jamais codé en dur rigide.
    france_travail_scope: str = "api_offresdemploiv2 o2dsoffre"  # FRANCE_TRAVAIL_SCOPE

    # --- Debug (corps d'erreur HTTP complet) — JOBTOOL_AGG_DEBUG=1 -----------
    agg_debug: bool = Field(default=False, validation_alias="JOBTOOL_AGG_DEBUG")

    # --- Chemins par défaut de l'agrégateur (surchargeables par la CLI) ------
    config_path: Path = Field(
        default=Path("data/aggregator/config.yaml"),
        validation_alias="JOBTOOL_AGG_CONFIG",
    )
    db_path: Path = Field(
        default=Path("data/aggregator/jobs.db"), validation_alias="JOBTOOL_AGG_DB"
    )
    export_dir: Path = Field(
        default=Path("output/aggregator/offres"), validation_alias="JOBTOOL_AGG_OUT"
    )

    # --- Base d'expériences (même défaut que le CLI cv/letter : data/moi.yaml) ----
    # Utilisée par la couche API pour la génération CV/lettre, via le même mécanisme
    # pydantic-settings que les autres chemins (aucun nouveau mécanisme de config).
    base_path: Path = Field(
        default=Path("data/moi.yaml"), validation_alias="JOBTOOL_BASE"
    )

    # --- Base de données applicative (Postgres/Supabase) ---------------------
    # Absente -> l'application retombe sur les fichiers SQLite locaux (mode mono-poste,
    # comportement historique). Présente -> tout passe par Postgres. Cette bascule est le
    # SEUL interrupteur : aucun code métier ne sait de quelle base il s'agit.
    database_url: str | None = None  # DATABASE_URL
    # Authentification : on vérifie la SIGNATURE des jetons via le JWKS public du projet
    # (clé asymétrique), donc aucun secret partagé à stocker ici.
    supabase_url: str | None = None  # SUPABASE_URL
    supabase_jwks_url: str | None = None  # SUPABASE_JWKS_URL
    # Clé « publiable » : destinée au navigateur, elle n'ouvre RIEN par elle-même (toute
    # lecture reste soumise à l'authentification). C'est le seul de ces éléments que le
    # front a le droit de connaître — d'où la route /auth/config qui la lui donne.
    supabase_publishable_key: str | None = None  # SUPABASE_PUBLISHABLE_KEY

    # --- Entretien du catalogue et collectes ---------------------------------
    # Une offre qu'aucune collecte n'a revue depuis N jours (alors que ses sources ont été
    # collectées avec succès entre-temps) est EXPIRÉE : retirée du catalogue, sauf si un
    # compte y tient (favori, candidature…), auquel cas elle est gardée et signalée.
    # 0 ou moins = aucune purge. Voir catalogue.purge_expired.
    catalogue_retention_days: int = Field(
        default=30, validation_alias="JOBTOOL_CATALOGUE_RETENTION_DAYS"
    )
    # Délai minimal entre deux collectes lancées depuis l'INTERFACE (la CLI n'y est pas
    # soumise) : le catalogue est commun, et une collecte coûte des centaines d'appels.
    collection_cooldown_minutes: int = Field(
        default=15, validation_alias="JOBTOOL_COLLECTION_COOLDOWN_MINUTES"
    )

    # --- Retouche manuelle des documents générés -----------------------------
    # Les brouillons (texte retouché à la main) sont de la DONNÉE : ils vivent sous
    # data/. Les PDF/aperçus recompilés sont des ARTEFACTS : ils vivent sous output/,
    # jetables et reconstructibles à tout moment depuis le brouillon.
    drafts_db_path: Path = Field(
        default=Path("data/documents/drafts.db"), validation_alias="JOBTOOL_DRAFTS_DB"
    )
    documents_dir: Path = Field(
        default=Path("output/documents"), validation_alias="JOBTOOL_DOCUMENTS_DIR"
    )
