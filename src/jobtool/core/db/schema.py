"""Schéma de la base applicative — définition UNIQUE, partagée par le code et Alembic.

Un seul endroit décrit les tables : les modules de stockage construisent leurs requêtes
sur ces objets `Table`, et Alembic compare ce même `metadata` à la base réelle pour
générer les migrations. Il n'existe donc pas de « schéma dans le code » et de « schéma
dans les migrations » qui pourraient diverger.

TYPES NEUTRES, volontairement : la suite de tests tourne sur SQLite (rapide, hors ligne)
et la production sur Postgres. On n'écrit donc aucun type propre à un moteur — sauf
`JSON_`, qui devient `jsonb` sur Postgres (indexable) et du JSON textuel sur SQLite.

HORODATAGES EN TEXTE ISO-8601 (et non en `DateTime`) : c'est déjà la convention de tout
le projet (`fetched_date`, `applied_at`, `created_at` sont des `str` dans les modèles
Pydantic). Les garder en texte évite une couche de conversion dans les deux sens, et le
tri lexicographique sur de l'ISO-8601 UTC donne exactement l'ordre chronologique.

MULTI-CLIENT — la décision structurante est le découpage en deux de l'ancienne table
`jobs` :
  - `offers`      : le CONTENU de l'annonce, mutualisé entre tous les comptes. Le
                    `dedup_hash` est calculé sur le contenu : deux clients qui croisent
                    la même annonce partagent la ligne, et donc l'agrégation qui l'a
                    ramenée.
  - `user_offers` : l'ÉTAT de cette annonce POUR UN COMPTE (favori, statut, date de
                    candidature, description corrigée). Sans cette séparation, le favori
                    d'Alice serait le favori de Bob.
La visibilité suit `user_offers` : une offre n'apparaît chez un client que s'il en a une
ligne, c'est-à-dire si SA recherche l'a ramenée. Le catalogue est mutualisé, pas la vue.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects import postgresql

#: JSON portable : `jsonb` sur Postgres (requêtable, indexable), JSON textuel ailleurs.
JSON_ = JSON().with_variant(postgresql.JSONB(), "postgresql")

metadata = MetaData()


# --- Comptes ----------------------------------------------------------------
# `id` est NOTRE identifiant, stable pour toujours ; `auth_id` est celui du fournisseur.
# On ne pose aucune clé étrangère vers le schéma `auth` de Supabase : il n'existe pas sur
# SQLite (tests), et lier nos migrations à une table gérée par un tiers rendrait le schéma
# non reproductible ailleurs.
users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    # Identifiant chez le FOURNISSEUR d'authentification (Supabase `auth.users.id`),
    # distinct de notre `id` interne. Ce découplage permet de changer de fournisseur, ou
    # de rattacher un compte créé avant l'authentification, sans toucher une seule clé
    # étrangère : c'est une correspondance, pas une identité.
    Column("auth_id", Text, nullable=True, unique=True),
    Column("email", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False, server_default=""),
    Column("is_admin", Boolean, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False),
    # Profil ACTIF du compte (administrateurs seulement, cf. `profiles`). NULL = le profil
    # principal. Pas de clé étrangère vers `profiles` : `profiles` référence déjà `users`, et
    # le cycle compliquerait la création des tables pour un gain nul — la résolution vérifie
    # de toute façon que le profil existe et appartient au compte.
    Column("active_profile_id", Uuid, nullable=True),
)


# --- Catalogue d'offres (MUTUALISÉ) -----------------------------------------
offers = Table(
    "offers",
    metadata,
    Column("dedup_hash", Text, primary_key=True),
    Column("id", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("native_id", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("company", Text, nullable=False),
    Column("location", Text, nullable=False),
    Column("remote", Text, nullable=False),
    Column("contract_type", Text, nullable=False),
    Column("salary", JSON_, nullable=True),
    Column("description", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("posted_date", Text, nullable=True),
    Column("fetched_date", Text, nullable=False),
    Column("url_key", Text, nullable=False, server_default=""),
    Column("raw", JSON_, nullable=False),
    # --- Champs NORMALISÉS par le code (aggregator/geo.py, aggregator/contracts.py) ---
    # La recherche filtre sur eux ; `location` / `contract_type` restent le texte de la
    # source. `norm_version` date la règle qui les a produits : quand elle évolue, les
    # lignes plus anciennes sont recalculées (storage.renormalize_catalogue).
    Column("country", Text, nullable=True),
    Column("region", Text, nullable=True),
    Column("department", Text, nullable=True),
    Column("city", Text, nullable=True),
    Column("place_precision", Text, nullable=False, server_default="inconnu"),
    Column("contract_kind", Text, nullable=False, server_default="inconnu"),
    Column("work_time", Text, nullable=True),
    Column("norm_version", Integer, nullable=False, server_default="0"),
    # --- Dédoublonnage (aggregator/dedup.py) -----------------------------------------
    # Clés de comparaison de l'offre CANONIQUE (entreprise et intitulé normalisés).
    Column("company_key", Text, nullable=False, server_default=""),
    Column("title_key", Text, nullable=False, server_default=""),
    # Dernière fois qu'une collecte a revu l'offre (max de ses occurrences) : base de
    # l'expiration du catalogue.
    Column("last_seen_at", Text, nullable=True),
    # Offre expirée mais CONSERVÉE parce qu'un compte y tient (favori, candidature,
    # document) : elle reste consultable, signalée comme telle.
    Column("expired_at", Text, nullable=True),
    # Offre PRIVÉE (saisie à la main) : visible de son seul auteur. NULL = catalogue
    # partagé. Pas de clé étrangère : la purge d'un compte ne doit pas buter dessus.
    Column("owner_user_id", Uuid, nullable=True),
    Index("idx_offers_url_key", "url_key"),
    Index("idx_offers_native", "source", "native_id"),
    Index("idx_offers_department", "department"),
    Index("idx_offers_contract_kind", "contract_kind"),
)

#: OCCURRENCES : chaque (source, identifiant natif) où une annonce a été vue, reliée à
#: son offre CANONIQUE (`offers`). C'est la donnée brute de chaque source — contenu,
#: liens, champs normalisés — à partir de laquelle l'offre canonique est recalculée
#: (aggregator/dedup.best_fields). Une occurrence n'appartient qu'à une offre : la clé
#: primaire est (source, native_id), et une fusion déplace l'occurrence.
#: `match_level` / `match_score` tracent POURQUOI elle a été rattachée à son offre.
offer_occurrences = Table(
    "offer_occurrences",
    metadata,
    Column("source", Text, primary_key=True),
    Column("native_id", Text, primary_key=True),
    Column("offer_id", Text, ForeignKey("offers.dedup_hash"), nullable=False),
    Column("url", Text, nullable=False, server_default=""),
    # Identité du lien principal (aggregator/dedup.offer_url_key). Toutes les identités
    # de l'occurrence (lien d'offre, de candidature, partenaires) sont dans
    # `occurrence_urls`, interrogeable par `IN` pour le dédoublonnage par URL.
    Column("url_key", Text, nullable=False, server_default=""),
    Column("title", Text, nullable=False, server_default=""),
    Column("company", Text, nullable=False, server_default=""),
    Column("location", Text, nullable=False, server_default=""),
    Column("remote", Text, nullable=False, server_default="inconnu"),
    Column("contract_type", Text, nullable=False, server_default=""),
    Column("salary", JSON_, nullable=True),
    Column("description", Text, nullable=False, server_default=""),
    Column("posted_date", Text, nullable=True),
    Column("raw", JSON_, nullable=False),
    Column("country", Text, nullable=True),
    Column("region", Text, nullable=True),
    Column("department", Text, nullable=True),
    Column("city", Text, nullable=True),
    Column("place_precision", Text, nullable=False, server_default="inconnu"),
    Column("contract_kind", Text, nullable=False, server_default="inconnu"),
    Column("work_time", Text, nullable=True),
    Column("company_key", Text, nullable=False, server_default=""),
    Column("title_key", Text, nullable=False, server_default=""),
    Column("norm_version", Integer, nullable=False, server_default="0"),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("match_level", Text, nullable=False, server_default="nouvelle"),
    Column("match_score", Float, nullable=True),
    Index("idx_occurrences_offer", "offer_id"),
    Index("idx_occurrences_url_key", "url_key"),
)

#: Index des identités d'URL d'une occurrence (une ligne par identité) : c'est lui que
#: le niveau 2 du dédoublonnage interroge. Une identité peut appartenir à plusieurs
#: occurrences — c'est précisément ce qui révèle un doublon.
occurrence_urls = Table(
    "occurrence_urls",
    metadata,
    Column("url_key", Text, primary_key=True),
    Column("source", Text, primary_key=True),
    Column("native_id", Text, primary_key=True),
)

#: Offres FUSIONNÉES : l'ancien identifiant renvoie à l'offre qui l'a absorbée. Un lien,
#: un favori ou un document qui citait l'ancien identifiant retrouve ainsi son offre.
#: `level` / `score` : pourquoi la fusion a été décidée (traçabilité).
offer_aliases = Table(
    "offer_aliases",
    metadata,
    Column("old_id", Text, primary_key=True),
    Column("offer_id", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("score", Float, nullable=True),
    Column("merged_at", Text, nullable=False),
    Index("idx_aliases_offer", "offer_id"),
)


#: COLLECTES : une ligne par collecte (CLI ou bouton « Actualiser les offres »). Sert de
#: VERROU (une seule collecte à la fois, et pas plus d'une par quart d'heure depuis
#: l'interface), de SUIVI (progression par source, lue par l'interface pendant que la
#: collecte tourne en arrière-plan) et d'HISTORIQUE (ce que chaque collecte a ajouté, ses
#: erreurs par source). Stockée en base, pas en mémoire : l'état survit à un redémarrage
#: et reste juste avec plusieurs processus.
collection_runs = Table(
    "collection_runs",
    metadata,
    Column("id", Text, primary_key=True),
    # running | done | failed | rejected (refusée par le verrou, jamais lancée)
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),  # api | cli
    Column("triggered_by", Uuid, nullable=True),  # compte à l'origine (API)
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text, nullable=True),
    Column("sources", JSON_, nullable=False),  # {source: avancement et bilan}
    Column("summary", JSON_, nullable=True),  # volumes et dédoublonnage de la collecte
    Column("errors", JSON_, nullable=False),  # erreurs globales (hors sources)
    Index("idx_collection_runs_started", "started_at"),
)


# --- État des offres PAR COMPTE ---------------------------------------------
# `description_override` : la correction manuelle de la description reste privée. La
# description mutualisée n'est jamais réécrite par un client (elle vient de la source).
user_offers = Table(
    "user_offers",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("dedup_hash", Text, ForeignKey("offers.dedup_hash"), primary_key=True),
    Column("status", Text, nullable=False, server_default="new"),
    Column("favorite", Boolean, nullable=False, server_default="0"),
    Column("applied_at", Text, nullable=True),
    Column("description_override", Text, nullable=True),
    Column("added_at", Text, nullable=False),
    # Date de MASQUAGE (ISO-8601), posée quand l'offre passe en `seen`. C'est elle qui date
    # la purge : sans elle, on ne saurait pas depuis combien de temps une offre est masquée,
    # et `added_at` répondrait à une autre question (quand elle est entrée dans le compte).
    # NULL = offre non masquée, ou masquée avant l'introduction de cette colonne.
    Column("hidden_at", Text, nullable=True),
    Index("idx_user_offers_status", "user_id", "status"),
    Index("idx_user_offers_favorite", "user_id", "favorite"),
    # Purge : on balaye par (statut, date de masquage). Sans cet index, le nettoyage
    # quotidien parcourrait tout le catalogue de chaque compte.
    Index("idx_user_offers_hidden", "user_id", "status", "hidden_at"),
)


# --- Base d'expériences (l'ancien data/moi.yaml) -----------------------------
# Une ligne par compte, le contenu en JSON : `ExperienceBase` est déjà un modèle Pydantic
# validé, avec ses ids stables qui portent la traçabilité. L'éclater en tables ferait
# réécrire cette validation sans rien apporter tant qu'on ne requête pas À L'INTÉRIEUR
# d'un CV. Postgres saura indexer le jsonb le jour où ce sera utile.
experience_bases = Table(
    "experience_bases",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("data", JSON_, nullable=False),
    Column("updated_at", Text, nullable=False),
)


# --- Documents retouchés (l'ancien document_drafts) --------------------------
documents = Table(
    "documents",
    metadata,
    Column("doc_id", Text, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("offer_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("template", Text, nullable=False),
    Column("render_input", JSON_, nullable=False),
    Column("original_texts", JSON_, nullable=False),
    Column("overrides", JSON_, nullable=False),
    # Mode pro : render_input réécrit EN ENTIER à la main. NULL = mode normal (render_input
    # d'origine + overrides). Non NULL, il est à lui seul l'entrée de la compilation, SANS
    # effacer les overrides — qui reviennent tels quels dès que la colonne repasse à NULL.
    Column("edited_input", JSON_, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    # Profil qui a servi à générer le document : "" = profil principal, sinon l'id d'une
    # ligne de `profiles`. Un document n'est repris et contrôlé que sous SON profil : ses
    # contrôles de traçabilité le comparent à la base d'expériences de ce profil-là.
    Column("profile_key", Text, nullable=False, server_default=""),
    Index("idx_documents_lookup", "user_id", "offer_id", "kind"),
)


# --- Consommation du modèle (facturation, quotas) ----------------------------
# Écrite dès maintenant, pas « plus tard » : chaque génération coûte de l'argent réel, et
# une consommation non enregistrée ne se rattrape jamais rétroactivement.
llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("kind", Text, nullable=False),  # cv | letter | …
    Column("model", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False),
    Index("idx_llm_usage_user", "user_id", "created_at"),
)


# --- Journal des échanges avec le modèle (onglet d'administration) -----------
# DEUX tables, et non une : la liste du journal ne doit transporter AUCUN prompt (un
# échange de CV pèse des dizaines de kilo-octets, un aller-retour SQL distant ~150 ms).
# `llm_runs` porte de quoi dresser la liste, `llm_exchanges` le contenu intégral qu'on ne
# charge qu'en ouvrant une génération.
#
# Ces lignes contiennent du CONTENU CLIENT (base d'expériences, description de l'offre,
# texte généré) : c'est assumé, la lecture est réservée à l'administration et la rétention
# est courte (services/llm_journal_service.py en garde 10 par type, toutes lignes
# confondues). `llm_usage` reste à côté : elle compte pour facturer, et survit à la purge.
llm_runs = Table(
    "llm_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column(
        "kind", Text, nullable=False
    ),  # cv | letter, même vocabulaire que `documents`
    Column("offer_id", Text, nullable=True),
    Column("template", Text, nullable=False, server_default=""),
    Column("model", Text, nullable=False, server_default=""),
    Column(
        "status", Text, nullable=False, server_default="running"
    ),  # running|ok|error
    Column("error", Text, nullable=True),
    Column("calls", Integer, nullable=False, server_default="0"),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text, nullable=True),
    # La purge et la liste parcourent exactement cet ordre : les dernières d'un type.
    Index("idx_llm_runs_kind", "kind", "started_at"),
)


llm_exchanges = Table(
    "llm_exchanges",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Text, ForeignKey("llm_runs.run_id"), nullable=False),
    Column("seq", Integer, nullable=False),  # rang de l'appel dans la génération
    Column(
        "step", Text, nullable=False, server_default=""
    ),  # routage|redaction|cv_a|cv_b
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("retries_429", Integer, nullable=False, server_default="0"),
    Column("messages", JSON_, nullable=False),  # ce qui est PARTI au modèle, intégral
    Column("response", JSON_, nullable=True),  # NULL : rien d'exploitable en retour
    Column("created_at", Text, nullable=False),
    Index("idx_llm_exchanges_run", "run_id", "seq"),
)


# --- Photo de profil (donnée PERSONNELLE) ------------------------------------
# Une ligne par compte, au plus. L'image est stockée NORMALISÉE (JPEG carré 600 px,
# ~45 Kio) : la validation a lieu à l'envoi, jamais au rendu — Typst ne sait pas
# rattraper une erreur d'image et rend un JPEG tronqué sans broncher, avec un aplat gris.
#
# En base plutôt que sur disque : l'état d'un compte vit déjà entièrement dans la base, et
# `DATABASE_URL` reste le seul interrupteur du mode. Des fichiers locaux au serveur
# seraient invisibles d'une autre instance en multi-comptes, et survivraient à la
# suppression du compte.
#
# `sha256` identifie la VERSION de l'image : c'est lui que porte le profil, et c'est lui
# qui nomme le fichier passé à Typst — deux comptes ne peuvent donc pas se voir attribuer
# la photo l'un de l'autre par collision de nom.
profile_photos = Table(
    "profile_photos",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("sha256", Text, nullable=False),
    Column("jpeg", LargeBinary, nullable=False),
    Column("updated_at", Text, nullable=False),
)


# --- Profils SUPPLÉMENTAIRES (administrateurs) --------------------------------
# Un compte a toujours son profil PRINCIPAL (`experience_bases`, ou le YAML en mono-poste),
# dont le stockage ne change pas. Un administrateur peut en créer d'autres : chacun est une
# base d'expériences complète (JSON, même forme que `experience_bases.data`) avec sa propre
# photo. La photo vit dans la ligne du profil : elle disparaît avec lui. La liste des profils
# ne lit jamais la colonne `photo_jpeg`.
profiles = Table(
    "profiles",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("data", JSON_, nullable=False),
    Column("photo_sha256", Text, nullable=True),
    Column("photo_jpeg", LargeBinary, nullable=True),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("idx_profiles_user", "user_id", "created_at"),
)


# --- Entretien guidé par LLM (construction du profil) ------------------------
# Une session par compte ET par profil : messages, propositions en attente ou tranchées, plan, résumé et
# provenance (élément du profil -> messages qui le fondent). En JSON, pour la même raison
# que `experience_bases` : l'état est lu et réécrit en entier à chaque tour (UNE écriture
# par tour), jamais requêté de l'intérieur. Contenu PERSONNEL : rattaché au compte.
interview_sessions = Table(
    "interview_sessions",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    # Un entretien PAR PROFIL : "" = profil principal (même convention que `documents`).
    Column("profile_key", Text, primary_key=True, server_default=""),
    Column("data", JSON_, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


# --- Clés d'API propres à un compte (cf. core/api_keys.py) --------------------
# Une ligne par compte ET par clé. La valeur est CHIFFRÉE (Fernet) : la base seule ne
# livre aucune clé. Donnée du compte : la clé étrangère la rattache à `users`.
api_keys = Table(
    "api_keys",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("name", Text, primary_key=True),
    Column("ciphertext", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


__all__ = [
    "api_keys",
    "collection_runs",
    "documents",
    "experience_bases",
    "interview_sessions",
    "llm_exchanges",
    "llm_runs",
    "llm_usage",
    "metadata",
    "offer_aliases",
    "offer_occurrences",
    "offers",
    "occurrence_urls",
    "profile_photos",
    "profiles",
    "user_offers",
    "users",
]
