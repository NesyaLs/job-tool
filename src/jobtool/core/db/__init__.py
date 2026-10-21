"""Couche base de données : schéma unique + fabrique de connexions.

Ne contient AUCUNE requête métier — celles-ci vivent dans les modules de stockage de
chaque domaine (aggregator/storage.py, documents/drafts/store.py), qui reçoivent une
`Connection` construite ici.
"""

from jobtool.core.db.bulk import chunked, insert_ignore
from jobtool.core.db.engine import (
    connect_sqlite,
    create_all,
    database_for,
    engine_for_sqlite_file,
    make_engine,
    open_connection,
)
from jobtool.core.db.identity import (
    LOCAL_USER_ID,
    AccountConflict,
    ensure_local_user,
    ensure_user,
    resolve_account,
)
from jobtool.core.db.schema import (
    api_keys,
    collection_runs,
    documents,
    experience_bases,
    interview_sessions,
    llm_exchanges,
    llm_runs,
    llm_usage,
    metadata,
    offer_aliases,
    occurrence_urls,
    offer_occurrences,
    offers,
    profile_photos,
    profiles,
    user_offers,
    users,
)

__all__ = [
    "LOCAL_USER_ID",
    "AccountConflict",
    "api_keys",
    "chunked",
    "collection_runs",
    "connect_sqlite",
    "create_all",
    "database_for",
    "documents",
    "engine_for_sqlite_file",
    "ensure_local_user",
    "ensure_user",
    "experience_bases",
    "insert_ignore",
    "interview_sessions",
    "llm_exchanges",
    "llm_runs",
    "llm_usage",
    "make_engine",
    "metadata",
    "offer_aliases",
    "occurrence_urls",
    "offer_occurrences",
    "offers",
    "profile_photos",
    "profiles",
    "open_connection",
    "resolve_account",
    "user_offers",
    "users",
]
