"""Fabrique de connexions : un moteur SQLAlchemy, pour Postgres comme pour SQLite.

Point d'entrée UNIQUE vers la base. Les modules de stockage reçoivent une `Connection`
et ne savent jamais à quel moteur ils parlent — c'est ce qui permet de faire tourner la
suite de tests sur SQLite (rapide, hors ligne) et la production sur Postgres sans une
seule ligne de code métier conditionnelle.

Deux réglages non évidents, expliqués ici pour ne pas être « nettoyés » plus tard :

1. `prepare_threshold=None` sur Postgres. Supabase expose un pooler pgbouncer en mode
   TRANSACTION : deux requêtes successives ne tombent pas forcément sur la même session
   serveur. Les *prepared statements* de psycopg, eux, sont attachés à une session — d'où
   des « prepared statement "_pg3_0" already exists » sporadiques sous concurrence. On les
   désactive : le pooler est incompatible avec, pas le pilote.

2. `check_same_thread=False` sur SQLite. FastAPI exécute les routes synchrones dans un
   pool de threads ; une connexion créée dans l'un et utilisée dans l'autre serait refusée.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn

from jobtool.core.db.identity import ensure_local_user
from jobtool.core.db.schema import metadata


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Construit le moteur pour une URL de base (postgresql+psycopg:// ou sqlite://)."""
    if url.startswith("sqlite"):
        engine = create_engine(
            url, echo=echo, connect_args={"check_same_thread": False}
        )

        # Les clés étrangères sont DÉSACTIVÉES par défaut sur SQLite : sans ça, les tests
        # laisseraient passer des lignes orphelines que Postgres refuserait en production.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,  # une connexion coupée par le pooler est remplacée, pas levée
        connect_args={"prepare_threshold": None},  # voir l'en-tête du module
    )


def database_for(database_url: str | None, sqlite_path: str | Path) -> Engine | str:
    """LA bascule de mode : moteur partagé si une URL de base est définie, sinon le fichier.

    Un seul endroit décide, pour que l'API et la CLI visent la MÊME base. La CLI passait
    longtemps le seul chemin SQLite : en mode Postgres, `jobtool aggregate` écrivait dans
    un fichier local que l'application ne lisait jamais.
    """
    if database_url:
        return make_engine(database_url)
    return str(sqlite_path)


def engine_for_sqlite_file(path: str | Path) -> Engine:
    """Moteur pointant un fichier SQLite (mode mono-poste, et tests). Crée le dossier."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    return make_engine(f"sqlite:///{file}")


def create_all(connection: Connection) -> None:
    """Met la base au niveau du schéma : tables manquantes, puis colonnes manquantes.

    Réservé au mode SQLite (tests, mono-poste) : sur Postgres, c'est Alembic qui fait foi.
    Créer les tables depuis le code ET par migration ferait deux vérités, dont une seule
    serait versionnée.
    """
    metadata.create_all(connection, checkfirst=True)
    _add_missing_columns(connection)
    _copy_legacy_offer_sources(connection)


def _add_missing_columns(connection: Connection) -> None:
    """Ajoute à une base SQLite EXISTANTE les colonnes apparues depuis sa création.

    `metadata.create_all` ne crée que les tables absentes : une base locale antérieure
    garderait sa forme d'origine et casserait à la première lecture d'une colonne ajoutée
    depuis. Il n'y a pas d'Alembic en mono-poste — le rattrapage se fait donc ici, DÉRIVÉ
    du schéma (jamais d'une liste tenue à la main).

    Seules les colonnes ajoutables telles quelles sont traitées (nullable, ou avec une
    valeur par défaut serveur). Une colonne obligatoire sans défaut demande de décider quoi
    mettre dans les lignes existantes : ça, c'est une vraie migration.
    """
    if connection.dialect.name != "sqlite":
        return
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.server_default is None:
                continue
            ddl = CreateColumn(column).compile(connection.engine).string
            connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
        # Index apparus depuis : même raison, `create_all` ne les pose que sur une table
        # qu'il crée. Un filtre sans son index reste juste, mais balaie toute la table.
        for index in table.indexes:
            index.create(connection, checkfirst=True)


#: Reprise de l'ancien modèle de provenance (`offer_sources`) vers les occurrences. Le
#: contenu de chaque occurrence est celui de son offre (l'ancien modèle ne gardait que
#: celui-là) ; ses champs normalisés sont à 0 (`norm_version`) et seront recalculés.
#: Une identité (source, native_id) suivie par plusieurs offres va à la plus petite ; les
#: autres, sans occurrence, sont fusionnées par `catalogue.deduplicate_catalogue`.
#: Le même SQL est figé dans la migration Alembic c7e2f9a4d1b6 (Postgres).
LEGACY_OCCURRENCES_SQL = (
    """
INSERT INTO offer_occurrences (
    source, native_id, offer_id, url, url_key, title, company, location, remote,
    contract_type, salary, description, posted_date, raw, place_precision,
    contract_kind, company_key, title_key, norm_version, first_seen_at, last_seen_at,
    match_level
)
SELECT s.source, s.native_id, s.dedup_hash, COALESCE(s.url, o.url, ''), '', o.title,
       o.company, o.location, o.remote, o.contract_type, o.salary, o.description,
       o.posted_date, o.raw, 'inconnu', 'inconnu', '', '', 0, s.fetched_date,
       s.fetched_date,
       CASE WHEN s.source = o.source AND s.native_id = o.native_id
            THEN 'nouvelle' ELSE 'reprise' END
FROM offer_sources s JOIN offers o ON o.dedup_hash = s.dedup_hash
WHERE s.dedup_hash = (
        SELECT MIN(s2.dedup_hash) FROM offer_sources s2
        WHERE s2.source = s.source AND s2.native_id = s.native_id)
  AND NOT EXISTS (
        SELECT 1 FROM offer_occurrences x
        WHERE x.source = s.source AND x.native_id = s.native_id)
""",
    """
INSERT INTO offer_occurrences (
    source, native_id, offer_id, url, url_key, title, company, location, remote,
    contract_type, salary, description, posted_date, raw, place_precision,
    contract_kind, company_key, title_key, norm_version, first_seen_at, last_seen_at,
    match_level
)
SELECT o.source, o.native_id, o.dedup_hash, o.url, '', o.title, o.company, o.location,
       o.remote, o.contract_type, o.salary, o.description, o.posted_date, o.raw,
       'inconnu', 'inconnu', '', '', 0, o.fetched_date, o.fetched_date, 'nouvelle'
FROM offers o
WHERE NOT EXISTS (SELECT 1 FROM offer_occurrences x WHERE x.offer_id = o.dedup_hash)
  AND NOT EXISTS (
        SELECT 1 FROM offer_occurrences x
        WHERE x.source = o.source AND x.native_id = o.native_id)
""",
    # Offres saisies à la main avant qu'elles soient privées : elles appartiennent au
    # compte qui les suit (une offre manuelle n'a jamais été suivie que par son auteur).
    """
UPDATE offers SET owner_user_id = (
    SELECT u.user_id FROM user_offers u WHERE u.dedup_hash = offers.dedup_hash
    ORDER BY u.added_at LIMIT 1)
WHERE source = 'manual' AND owner_user_id IS NULL
""",
)


def _copy_legacy_offer_sources(connection: Connection) -> None:
    """Base SQLite locale d'avant les occurrences : recopie sa provenance (idempotent).

    Sans cela, les offres déjà collectées n'auraient aucune occurrence : elles ne
    pourraient ni être dédoublonnées ni retrouver leurs liens. Postgres, lui, est repris
    par la migration Alembic.
    """
    if connection.dialect.name != "sqlite":
        return
    tables = set(inspect(connection).get_table_names())
    if "offer_sources" not in tables:
        # Base créée après le passage aux occurrences : seules les offres sans
        # occurrence (aucune en principe) sont concernées.
        for statement in LEGACY_OCCURRENCES_SQL[1:]:
            connection.execute(text(statement))
        return
    for statement in LEGACY_OCCURRENCES_SQL:
        connection.execute(text(statement))


def connect_sqlite(db_path: str | Path) -> Connection:
    """Ouvre une base SQLite locale, crée le schéma au besoin, garantit le compte local.

    Chemin MONO-POSTE (et tests) uniquement. Sur Postgres, le schéma est la propriété
    d'Alembic et les comptes celle de l'authentification : y créer des tables depuis le
    code ferait deux vérités dont une seule serait versionnée.

    Vit ICI, et non dans un module de stockage, parce que les offres ET les documents en
    ont besoin — et qu'un domaine n'importe jamais un autre domaine. Les deux en avaient
    chacun leur copie, au mot près.
    """
    conn = engine_for_sqlite_file(db_path).connect()
    create_all(conn)
    ensure_local_user(conn)  # les tables référencent `users` : il doit exister
    conn.commit()
    return conn


def open_connection(db: str | Path | Engine) -> Connection:
    """Connexion au moteur fourni, ou au fichier SQLite local — au choix de l'appelant.

    Point d'entrée UNIQUE des appelants qui peuvent tourner dans les deux modes (une
    agrégation lancée depuis la CLI sur un fichier local, ou depuis l'API sur la base
    partagée). Sans lui, chacun refait le test `isinstance(..., Engine)` et le premier qui
    l'oublie écrit dans l'autre base — silencieusement, puisque les deux fonctionnent.

    Ce module l'annonçait déjà quand il vivait dans `aggregator/storage` ; il y était
    pourtant réécrit à trois autres endroits, faute d'être accessible depuis `documents/`.
    """
    if isinstance(db, Engine):
        return db.connect()
    return connect_sqlite(db)
