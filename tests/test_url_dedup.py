"""Clé de déduplication par URL : normalisation + empreinte, et 3ᵉ filet de `upsert_job`.

Une offre saisie À LA MAIN n'a pas d'identifiant de source : son seul identifiant fiable est
le LIEN de l'annonce. On en dérive `native_id = url_native_id(url)`. Comme ce native_id ne
protège que DANS une source (`_native_id_known` compare (source, native_id)), un 3ᵉ filet
compare l'URL normalisée TOUTES SOURCES CONFONDUES : une offre ajoutée à la main puis
récupérée plus tard par l'agrégateur ne crée pas de doublon.
"""

from __future__ import annotations

from jobtool.aggregator import storage
from jobtool.aggregator.models import RawJob
from jobtool.aggregator.normalize import normalize_url, raw_to_job, url_native_id

# --- Normalisation de l'URL -------------------------------------------------


def test_query_string_is_ignored():
    """Les paramètres de suivi ne changent pas l'identité de l'annonce."""
    base = "https://exemple.fr/offres/123"
    assert normalize_url(base + "?utm_source=newsletter") == normalize_url(base)
    assert url_native_id(base + "?a=1&b=2") == url_native_id(base)


def test_fragment_trailing_slash_and_case_are_ignored():
    variants = [
        "https://Exemple.FR/offres/123",
        "https://exemple.fr/offres/123/",
        "https://exemple.fr/offres/123#description",
        "HTTPS://exemple.fr/offres/123",
    ]
    keys = {normalize_url(v) for v in variants}
    assert len(keys) == 1, f"toutes ces variantes désignent la même annonce : {keys}"


def test_default_port_is_ignored():
    assert normalize_url("https://exemple.fr:443/o/1") == normalize_url(
        "https://exemple.fr/o/1"
    )


def test_different_host_or_path_differ():
    a = url_native_id("https://exemple.fr/offres/123")
    b = url_native_id("https://exemple.fr/offres/124")
    c = url_native_id("https://autre.fr/offres/123")
    assert len({a, b, c}) == 3, "hôte ou chemin différent -> empreinte différente"


def test_path_case_is_significant():
    """L'hôte est insensible à la casse, le CHEMIN ne l'est pas (RFC 3986)."""
    assert normalize_url("https://exemple.fr/Offres/A") != normalize_url(
        "https://exemple.fr/offres/a"
    )


def test_empty_url_has_no_key():
    """Sans URL, pas de clé : le filet ne doit pas confondre toutes les offres sans lien."""
    assert normalize_url("") == ""
    assert url_native_id("") == ""


# --- 3ᵉ filet dans upsert_job ------------------------------------------------


def _job(source: str, native_id: str, title: str, url: str, company: str = "Acme"):
    return raw_to_job(
        RawJob(
            source=source,
            native_id=native_id,
            title=title,
            company=company,
            location="Paris",
            url=url,
        ),
        fetched_date="2026-08-11T09:00:00+00:00",
    )


def test_same_url_across_sources_is_not_duplicated(tmp_db):
    """Offre manuelle puis MÊME annonce vue par l'agrégateur -> une seule ligne."""
    conn = storage.connect(tmp_db)
    try:
        url = "https://candidat.francetravail.fr/offres/recherche/detail/999"
        manual = _job("manual", url_native_id(url), "Data Scientist", url)
        assert storage.upsert_job(conn, storage.LOCAL_USER_ID, manual) is True, (
            "1re insertion : offre inédite"
        )

        # Même annonce, autre source, autre native_id, TITRE DIFFÉRENT (le dedup_hash de
        # contenu ne protégerait donc pas) — mais même URL (à query près).
        later = _job(
            "france_travail",
            "FT-999",
            "Data Scientist (H/F) — CDI",
            url + "?utm_campaign=alerte",
            company="Acme Group",
        )
        assert storage.upsert_job(conn, storage.LOCAL_USER_ID, later) is False, (
            "même URL -> pas de doublon"
        )
        assert storage.count_jobs(conn, storage.LOCAL_USER_ID) == 1
    finally:
        conn.close()


def test_offers_without_url_are_not_merged(tmp_db):
    """Deux offres SANS lien restent distinctes (l'URL vide n'est pas une identité)."""
    conn = storage.connect(tmp_db)
    try:
        storage.upsert_job(
            conn,
            storage.LOCAL_USER_ID,
            _job("adzuna", "A-1", "Poste A", "", company="Un"),
        )
        storage.upsert_job(
            conn,
            storage.LOCAL_USER_ID,
            _job("adzuna", "A-2", "Poste B", "", company="Deux"),
        )
        assert storage.count_jobs(conn, storage.LOCAL_USER_ID) == 2, (
            "aucune fusion sur URL vide"
        )
    finally:
        conn.close()


def test_distinct_urls_still_insert(tmp_db):
    conn = storage.connect(tmp_db)
    try:
        storage.upsert_job(
            conn, storage.LOCAL_USER_ID, _job("manual", "m1", "A", "https://x.fr/1")
        )
        storage.upsert_job(
            conn, storage.LOCAL_USER_ID, _job("manual", "m2", "B", "https://x.fr/2")
        )
        assert storage.count_jobs(conn, storage.LOCAL_USER_ID) == 2
    finally:
        conn.close()
