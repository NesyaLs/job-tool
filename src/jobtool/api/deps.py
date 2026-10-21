"""Fournisseurs de dépendances FastAPI (composition root de l'API).

Centralise la construction des services à partir du settings existant. Les TESTS
surchargent ces fonctions via `app.dependency_overrides` pour injecter une base jetable et
un générateur stubé — aucun réseau ni LLM réel. Aucune logique métier ici : du câblage.

DEUX MODES, un seul interrupteur (`DATABASE_URL` dans le settings) :
  - absente  -> fichiers SQLite locaux, compte unique du poste. C'est le mode historique,
                celui de la suite de tests et d'un usage mono-poste.
  - présente -> base Postgres partagée, une ligne `users` par compte.
Aucun service ne connaît ce choix : il reçoit un moteur ou un chemin, et une identité.

Les DONNÉES d'un compte — offres, documents, ET base d'expériences — passent toutes par une
dépendance qui reçoit son identité. La base d'expériences a longtemps fait exception : elle
arrivait par un chemin de fichier lu dans le settings, donc identique pour tous les comptes.

L'IDENTITÉ est résolue en un seul point (`get_current_user_id`), à partir du jeton de la
requête, et INJECTÉE dans les services par `Depends`. Elle n'est jamais appelée à la main :
une dépendance appelée directement sort du graphe FastAPI — elle ne reçoit plus l'en-tête
de la requête, ne peut plus être surchargée en test, et ferait silencieusement autre chose
que ce que la signature annonce.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy import Engine

from jobtool.api.auth import authenticate, bearer_token
from jobtool.api.generation import Generator
from jobtool.core.api_keys import ApiKeyStore, with_account_keys
from jobtool.core.db import engine_for_sqlite_file, make_engine
from jobtool.core.experience_base.photo_store import PhotoStore
from jobtool.core.experience_base.profiles import ActiveProfile, resolve_active_profile
from jobtool.core.experience_base.store import ExperienceBaseRepo, experience_base_repo
from jobtool.interview.store import InterviewSessionStore
from jobtool.services.admin_service import AdminService
from jobtool.services.bullet_suggestion_service import BulletSuggestionService
from jobtool.services.discovery_service import DiscoveryService
from jobtool.services.document_edit_service import DocumentEditService
from jobtool.services.document_jobs import DocumentJobs
from jobtool.services.interview_service import InterviewService
from jobtool.services.offers_service import OffersService
from jobtool.services.profile_service import ProfileService
from jobtool.services.profiles_service import ProfilesService
from jobtool.services.llm_journal_service import GenerationJournal, journal_for
from jobtool.services.usage_service import UsageRecorder, recorder_for
from jobtool.settings import AggregatorSettings


@lru_cache
def get_settings() -> AggregatorSettings:
    """Settings pydantic (lit l'environnement + .env). Mis en cache pour le process."""
    return AggregatorSettings()


@lru_cache
def get_engine() -> Engine | None:
    """Moteur Postgres partagé, ou None si l'application tourne en mono-poste.

    Mis en cache : un moteur porte un POOL de connexions, en recréer un par requête
    annulerait tout l'intérêt du pool.
    """
    url = get_settings().database_url
    return make_engine(url) if url else None


def get_current_user_id(
    authorization: str | None = Depends(bearer_token),
) -> uuid.UUID:
    """Compte de LA REQUÊTE, déduit du jeton `Authorization: Bearer …`.

    JAMAIS mise en cache pour le process : l'identité dépend de la requête, un cache y
    figerait le premier appelant pour tous les autres.

    Sans authentification configurée (mono-poste, tests), renvoie le compte local — c'est
    le même interrupteur unique que pour la base. Voir api/auth.py.
    """
    return authenticate(get_engine(), get_settings(), authorization)


def get_offers_db() -> Engine | str:
    """Base des offres : moteur partagé, ou chemin du fichier SQLite local.

    Exposée comme dépendance (et pas seulement utilisée en interne) parce que l'agrégation
    écrit dans CETTE base et doit viser exactement la même que la lecture — les deux modes
    fonctionnent isolément, si bien qu'une divergence ne se manifeste que par une liste
    vide après une collecte annoncée réussie.
    """
    engine = get_engine()
    return engine if engine is not None else str(get_settings().db_path)


def get_offers_service(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
) -> OffersService:
    """Service d'offres, cloisonné sur le compte de la requête.

    La base arrive par `Depends`, comme l'identité et pour la même raison : appelée à la
    main, `get_offers_db` sortirait du graphe FastAPI et ne serait plus surchargeable —
    la lecture pourrait alors viser une autre base que l'écriture sans que rien ne le dise.
    """
    return OffersService(db, user_id)


def get_discovery_service(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
) -> DiscoveryService:
    """Pile de la page « Découvrir », sur le compte ET la base de la requête (par `Depends`,
    comme `get_offers_service` : la pile doit lire l'état que les gestes viennent d'écrire)."""
    return DiscoveryService(db, user_id)


def get_active_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
) -> ActiveProfile:
    """Droits aux profils multiples ET profil actif du compte, résolus en UNE requête.

    Tout ce qui dépend du profil (base d'expériences, photo, documents, entretien) passe par
    ici, par `Depends` : un compte non administrateur, ou dont le profil actif n'existe plus,
    retombe sur son profil principal au même endroit pour toutes les routes.
    """
    return resolve_active_profile(db, user_id)


def get_profile_key(active: ActiveProfile = Depends(get_active_profile)) -> str:
    """Clé du profil actif de la requête ("" = profil principal)."""
    return active.key


def get_experience_base_repo(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    profile_key: str = Depends(get_profile_key),
) -> ExperienceBaseRepo:
    """Base d'expériences DU COMPTE de la requête : une ligne en base, ou le fichier local.

    L'identité arrive par `Depends`, comme partout ailleurs et pour la même raison. Elle
    manquait ici : les trois fabriques ci-dessous construisaient leur service à partir du
    seul `settings.base_path`, c'est-à-dire du MÊME fichier serveur pour tous les comptes.
    Sur la base partagée, chacun lisait et écrasait le profil des autres.

    Même interrupteur unique que le reste (`DATABASE_URL`, via `get_offers_db`) : aucun
    service ne sait duquel des deux modes il relève.
    """
    return experience_base_repo(
        db, user_id, yaml_path=get_settings().base_path, profile_key=profile_key
    )


def get_usage_recorder(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
) -> UsageRecorder:
    """Enregistreur de consommation du modèle, câblé sur le compte de la requête.

    Injecté sous forme de FONCTION, comme les autres callbacks du projet : les services de
    génération notent « un appel de ce type » sans connaître ni base ni compte, et restent
    utilisables hors API (CLI, tests) où l'enregistreur par défaut n'écrit rien.
    """
    return recorder_for(db, user_id)


def get_generation_journal(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
) -> GenerationJournal:
    """Journal des échanges avec le modèle, câblé sur le compte de la requête.

    Même forme d'injection que l'enregistreur de consommation, et pour la même raison : les
    services de génération conservent leurs échanges sans connaître ni base ni compte, et
    restent utilisables hors API (CLI, tests) où le journal par défaut n'écrit rien.
    """
    return journal_for(db, user_id)


def get_api_key_store(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    settings: AggregatorSettings = Depends(get_settings),
) -> ApiKeyStore:
    """Clés d'API saisies par le compte de la requête (chiffrées en base)."""
    return ApiKeyStore(db, user_id, settings)


def get_account_settings(
    store: ApiKeyStore = Depends(get_api_key_store),
    settings: AggregatorSettings = Depends(get_settings),
) -> AggregatorSettings:
    """Settings du serveur où les clés du COMPTE remplacent celles du `.env`.

    Tout ce qui appelle le modèle ou une source d'offres pour le compte passe par ici : une
    clé saisie dans l'interface sert à son propriétaire, et à lui seul.
    """
    return with_account_keys(settings, store.load())


def get_generator(
    base_repo: ExperienceBaseRepo = Depends(get_experience_base_repo),
    record_usage: UsageRecorder = Depends(get_usage_recorder),
    settings: AggregatorSettings = Depends(get_account_settings),
) -> Generator:
    """Générateur CV/lettre, sur la base d'expériences ET la clé du compte de la requête."""
    return Generator(
        base_repo, record_usage=record_usage, api_key=settings.mistral_api_key
    )


def get_drafts_db() -> Engine | Path:
    """Base des documents : moteur partagé, ou fichier SQLite À PART en mono-poste.

    Une dépendance, et non un calcul refait par chaque fabrique : la retouche des documents
    et la suppression d'un profil (qui emporte ses documents) doivent viser la MÊME base, et
    un test doit pouvoir la surcharger à un seul endroit.
    """
    engine = get_engine()
    return engine if engine is not None else get_settings().drafts_db_path


def get_document_edit_service(
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_key: str = Depends(get_profile_key),
    base_repo: ExperienceBaseRepo = Depends(get_experience_base_repo),
    record_usage: UsageRecorder = Depends(get_usage_recorder),
    journal: GenerationJournal = Depends(get_generation_journal),
    drafts_db: Engine | Path = Depends(get_drafts_db),
    settings: AggregatorSettings = Depends(get_account_settings),
) -> DocumentEditService:
    """Service de retouche manuelle, cloisonné sur le compte ET le profil de la requête."""
    return DocumentEditService(
        base_repo=base_repo,
        drafts_db=drafts_db,
        work_root=settings.documents_dir,
        user_id=user_id,
        record_usage=record_usage,
        journal=journal,
        profile_key=profile_key,
        api_key=settings.mistral_api_key,
    )


@lru_cache
def get_document_jobs() -> DocumentJobs:
    """Registre des générations en arrière-plan, UN pour le processus (cf. document_jobs).

    Mis en cache : les tâches doivent survivre à la requête qui les lance, pour être
    suivies par les suivantes. Chaque tâche porte son compte et son profil.
    """
    return DocumentJobs()


def get_photo_store(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    profile_key: str = Depends(get_profile_key),
) -> PhotoStore:
    """Photo de profil DU COMPTE de la requête.

    L'identité et la base arrivent par `Depends`, comme partout : une dépendance appelée à
    la main sort du graphe FastAPI, ne voit plus l'en-tête de la requête et ne peut plus
    être surchargée en test — c'est ainsi qu'on servirait la photo d'un autre compte.
    """
    return PhotoStore(db, user_id, profile_key=profile_key)


def get_profile_service(
    base_repo: ExperienceBaseRepo = Depends(get_experience_base_repo),
    photos: PhotoStore = Depends(get_photo_store),
) -> ProfileService:
    """Service profil, sur la base d'expériences ET la photo du compte de la requête."""
    return ProfileService(base_repo, photos)


def get_llm_client_factory(
    settings: AggregatorSettings = Depends(get_account_settings),
) -> Callable[[], object]:
    """Fabrique du client du modèle. Une FONCTION, appelée seulement au moment d'un tour :
    consulter l'entretien ou trancher une proposition marche sans clé d'API. La clé est
    celle du compte, sinon celle du serveur. Les tests la surchargent pour injecter un
    client simulé (aucun appel réseau dans la suite)."""
    from jobtool.core.llm.client import make_client

    api_key = settings.mistral_api_key
    return lambda: make_client(api_key)


def get_interview_service(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    profile_key: str = Depends(get_profile_key),
    base_repo: ExperienceBaseRepo = Depends(get_experience_base_repo),
    client_factory: Callable[[], object] = Depends(get_llm_client_factory),
    record_usage: UsageRecorder = Depends(get_usage_recorder),
    journal: GenerationJournal = Depends(get_generation_journal),
) -> InterviewService:
    """Entretien guidé DU COMPTE de la requête : sa session, sa base d'expériences.

    Identité, base et client arrivent tous par `Depends` : la session d'entretien contient
    le récit du parcours de la personne, et le profil qu'elle alimente est le sien.
    """
    return InterviewService(
        base_repo,
        InterviewSessionStore(db, user_id, profile_key=profile_key),
        client_factory,
        record_usage=record_usage,
        journal=journal,
    )


def get_bullet_suggestion_service(
    base_repo: ExperienceBaseRepo = Depends(get_experience_base_repo),
    client_factory: Callable[[], object] = Depends(get_llm_client_factory),
    record_usage: UsageRecorder = Depends(get_usage_recorder),
    journal: GenerationJournal = Depends(get_generation_journal),
) -> BulletSuggestionService:
    """Propositions de puces, sur la base d'expériences du compte de la requête."""
    return BulletSuggestionService(
        base_repo, client_factory, record_usage=record_usage, journal=journal
    )


def get_profiles_service(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Engine | str = Depends(get_offers_db),
    active: ActiveProfile = Depends(get_active_profile),
    drafts_db: Engine | Path = Depends(get_drafts_db),
) -> ProfilesService:
    """Gestion des profils DU COMPTE de la requête (droits déjà résolus dans `active`)."""
    settings = get_settings()
    return ProfilesService(
        db,
        user_id,
        active,
        yaml_path=settings.base_path,
        drafts_db=drafts_db,
        work_root=settings.documents_dir,
    )


def get_admin_service() -> AdminService:
    """Service d'administration. En mono-poste, il lit le même fichier SQLite que le reste,
    et le poste local y est administrateur d'office."""
    engine = get_engine()
    if engine is None:
        sqlite = engine_for_sqlite_file(get_settings().db_path)
        return AdminService(sqlite, single_user=True)
    return AdminService(engine)
