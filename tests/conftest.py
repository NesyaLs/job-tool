"""Fixtures partagées de la suite.

Le paquet `jobtool` est installé en editable (`pip install -e ".[dev]"`) : aucun
`sys.path.insert` n'est nécessaire pour l'importer. Les données d'exemple (base, CV,
offres) et les fixtures réseau (FakeSession) vivent ici pour être réutilisées sans
duplication par les tests issus des anciens scripts de preuve.

Plus aucun script n'est chargé depuis `scripts/` : les commandes vivent toutes dans le
paquet, et la fixture qui rendait `scripts/` importable a disparu avec elles.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# --- Isolation des DONNÉES : la suite n'écrit JAMAIS dans les vraies ---------
# `.env` contient DATABASE_URL et SUPABASE_JWKS_URL. Sans ce garde-fou, tout code appelant
# `AggregatorSettings()` — donc `get_engine()` de l'API — se connecterait à la base de
# PRODUCTION pendant les tests, et pourrait y écrire. Une variable d'environnement vide
# l'emporte sur le fichier .env : les settings retombent alors sur le mode SQLite local,
# qui est le mode voulu pour la suite.
#
# `JOBTOOL_BASE` relève du même garde-fou, et il manquait. En mode mono-poste — donc sous
# test — la base d'expériences est un FICHIER désigné par ce réglage, dont le défaut est
# `data/moi.yaml` : la vraie base de l'utilisateur. Un test qui appelle `PUT /profile` sans surcharger la
# dépendance réécrit ce fichier de bout en bout. Ce n'est pas hypothétique : c'est arrivé.
# On le pointe donc vers un fichier jetable.
#
# Les tests qui ont besoin d'une base complète lisent la fixture anonymisée `MOI_YAML`
# ci-dessous (tests/fixtures/profil_demo.yaml), jamais par les settings : `data/moi.yaml`
# est une donnée personnelle, absente du dépôt.
#
# La base de l'AGRÉGATEUR relève du même garde-fou, et c'est arrivé aussi : quand la
# collecte est passée en arrière-plan (inscription AVANT le pipeline), des tests qui
# remplaçaient le pipeline par son ancien nom ont laissé tourner le vrai — dans
# `data/aggregator/jobs.db`, avec les vraies clés du `.env` et le vrai config.yaml : une
# collecte réelle complète, écrite dans la base locale. D'où, pour toute la suite :
#   - la base, le dossier d'export et la config de collecte pointent vers du jetable ;
#   - les clés France Travail / Adzuna sont VIDES : ces sources s'auto-isolent ;
#   - la config jetable n'a ni entreprise SmartRecruiters ni site Workday.
# Un test qui oublie de remplacer un connecteur échoue ou ne ramène rien : il ne peut plus
# ni appeler le réseau, ni écrire dans les vraies données.
#
# Portée session et `autouse` : aucun test n'a à y penser, et aucun ne peut l'oublier.


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_real_data(tmp_path_factory):
    jetable = tmp_path_factory.mktemp("base_xp") / "moi.yaml"
    agg = tmp_path_factory.mktemp("agregateur")
    config = agg / "config.yaml"
    config.write_text(
        "criteres:\n  motsCles: data\n  max_results: 5\n", encoding="utf-8"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", "")
        mp.setenv("SUPABASE_JWKS_URL", "")  # idem : pas d'authentification en test
        mp.setenv("JOBTOOL_BASE", str(jetable))
        mp.setenv("JOBTOOL_AGG_DB", str(agg / "jobs.db"))
        mp.setenv("JOBTOOL_AGG_OUT", str(agg / "offres"))
        mp.setenv("JOBTOOL_AGG_CONFIG", str(config))
        # Clé de chiffrement des clés d'API : jetable, jamais celle de data/.secret_key.
        mp.setenv("JOBTOOL_SECRET_KEY", "")
        mp.setenv("JOBTOOL_SECRET_KEY_FILE", str(agg / ".secret_key"))
        for key in (
            "FRANCE_TRAVAIL_CLIENT_ID",
            "FRANCE_TRAVAIL_CLIENT_SECRET",
            "ADZUNA_APP_ID",
            "ADZUNA_APP_KEY",
        ):
            mp.setenv(key, "")
        from jobtool.api import deps

        deps.get_settings.cache_clear()
        deps.get_engine.cache_clear()
        yield
        deps.get_settings.cache_clear()
        deps.get_engine.cache_clear()


# --- Emplacements -----------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

MOI_YAML = FIXTURES_DIR / "profil_demo.yaml"
BASE_EXAMPLE_YAML = DATA_DIR / "base.example.yaml"
TAILORED_EXAMPLE_JSON = DATA_DIR / "tailored.example.json"


# --- Fixtures réseau (Adzuna / connecteurs HTTP) ----------------------------
class FakeResponse:
    """Réponse `requests` factice : statut + payload JSON (ou Exception à lever)."""

    def __init__(self, status_code: int, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = (
            json.dumps(json_data)
            if isinstance(json_data, (dict, list))
            else str(json_data)
        )

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class FakeSession:
    """Session `requests` factice : programme les réponses successives (GET et/ou POST).

    Les `outcomes` sont consommés dans l'ORDRE des appels, GET et POST confondus (`_idx`
    partagé) : on peut donc interleaver un listing POST et ses détails GET (cas Workday).
    `get_calls` reste le compteur de GET (rétrocompat des tests existants) ; `post_calls`
    et `last_json` exposent le POST.
    """

    def __init__(self, outcomes: list):
        self.get_calls = 0
        self.post_calls = 0
        self.last_params: dict | None = None
        self.params_history: list[dict] = []  # params de CHAQUE GET, dans l'ordre
        self.last_json: Any = None
        self._idx = 0
        self._outcomes = list(outcomes)

    def _next(self):
        outcome = self._outcomes[min(self._idx, len(self._outcomes) - 1)]
        self._idx += 1
        if isinstance(outcome, Exception):
            raise outcome
        status, data = outcome
        return FakeResponse(status, data)

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls += 1
        self.last_params = dict(params or {})
        self.params_history.append(self.last_params)
        return self._next()

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls += 1
        self.last_json = json
        return self._next()


@pytest.fixture
def fake_session():
    """Renvoie la classe FakeSession (à instancier avec la liste des réponses programmées)."""
    return FakeSession


# --- Données d'exemple ------------------------------------------------------
@pytest.fixture
def load_fixture():
    """Callable : charge un JSON de tests/fixtures/ par nom de fichier."""

    def _load(name: str) -> dict:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def moi_base():
    """Base complète anonymisée (tests/fixtures/profil_demo.yaml) — preuves de lettre."""
    from jobtool.core.experience_base.loader import load_base

    return load_base(MOI_YAML)


@pytest.fixture
def example_base():
    """Base d'exemple (data/base.example.yaml) — preuves CV / rendu."""
    from jobtool.core.experience_base.loader import load_base

    return load_base(BASE_EXAMPLE_YAML)


@pytest.fixture
def example_cv():
    """CV taillé d'exemple (data/tailored.example.json), cohérent avec example_base."""
    from jobtool.documents.cv.schema import TailoredCV

    return TailoredCV.model_validate_json(
        TAILORED_EXAMPLE_JSON.read_text(encoding="utf-8")
    )


@pytest.fixture
def expected_ats_strings():
    """Callable (base, cv) -> liste de chaînes attendues dans le texte extrait du PDF."""
    from jobtool.documents.cv.ats_targets import expected_strings

    return expected_strings


# --- Base SQLite jetable ----------------------------------------------------
@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Chemin d'une base SQLite JETABLE (jamais la vraie base)."""
    return tmp_path / "jobs.db"


def _seed_raw(source, native_id, title, company, location):
    from jobtool.aggregator.models import RawJob

    return RawJob(
        source=source,
        native_id=native_id,
        title=title,
        company=company,
        location=location,
        contract_type="Stage",
        description=f"Description de « {title} » chez {company}.",
        url=f"https://exemple.fr/{source}/{native_id}",
    )


@pytest.fixture
def seed_offers():
    """Callable seed(db_path) : peuple une base jetable d'offres factices via le VRAI storage.

    Passe par `normalize.raw_to_job` + `storage.upsert_job` (mêmes points d'entrée que le
    pipeline réel) pour ne pas dupliquer la normalisation/dédup. Variété pensée pour les
    filtres : 2 sources, 4 statuts, une offre « ancienne » (40 j) pour la fraîcheur.
    """
    from jobtool.aggregator import storage
    from jobtool.aggregator.models import Job, JobStatus
    from jobtool.aggregator.normalize import raw_to_job

    def seed(db_path) -> list:
        now = datetime.now(timezone.utc)
        today = now.isoformat()
        old = (now - timedelta(days=40)).isoformat()
        specs = [
            (
                _seed_raw(
                    "france_travail",
                    "FT-1",
                    "Ingénieur Calcul Haute Performance",
                    "Helix Compute",
                    "Grenoble",
                ),
                JobStatus.new,
                today,
            ),
            (
                _seed_raw("france_travail", "FT-2", "Data Scientist", "LCL", "Paris"),
                JobStatus.new,
                today,
            ),
            (
                _seed_raw("jobboard_x", "X-1", "Data Engineer", "Globex", "Paris"),
                JobStatus.seen,
                old,
            ),
            (
                _seed_raw(
                    "france_travail", "FT-3", "Développeur Python", "Acme", "Lyon"
                ),
                JobStatus.applied,
                today,
            ),
        ]
        conn = storage.connect(db_path)
        jobs: list[Job] = []
        try:
            for raw, status, fetched in specs:
                job = raw_to_job(raw, fetched_date=fetched)
                storage.upsert_job(conn, storage.LOCAL_USER_ID, job)
                if status is not JobStatus.new:
                    storage.set_status(
                        conn, storage.LOCAL_USER_ID, job.dedup_hash, status
                    )
                jobs.append(job)
        finally:
            conn.close()
        return jobs

    return seed


@pytest.fixture
def seed_dated_offers():
    """Callable seed(db_path) -> dict titre->posted_date : offres aux DATES DE PUBLICATION variées.

    Complète `seed_offers` (dont les offres n'ont aucun posted_date) pour éprouver le TRI par
    date de publication : trois dates distinctes + une offre SANS date (cas des sources qui n'en
    fournissent pas, et des futures offres manuelles). `fetched_date` est volontairement en ordre
    INVERSE du posted_date : un test qui trie par publication ne peut pas passer par accident en
    triant par date de récupération.

    Les dates sont RELATIVES à maintenant, et non écrites en clair. Le filtre de fraîcheur
    (`max_age_days`) les compare à `datetime.now()` : des dates absolues font passer la suite
    au vert le jour où on l'écrit, puis au rouge quelques semaines plus tard, sans qu'une
    seule ligne de code ait bougé. C'est exactement ce qui est arrivé ici.

    Les ÉCARTS, eux, restent lisibles et fixes : ils encadrent les seuils que les tests
    interrogent (7, 60 et 1000 jours).
    """
    from jobtool.aggregator import storage
    from jobtool.aggregator.models import RawJob
    from jobtool.aggregator.normalize import raw_to_job

    now = datetime.now(UTC)

    def _il_y_a(jours: float) -> str:
        return (now - timedelta(days=jours)).isoformat()

    # (titre, posted_date) du plus RÉCEMMENT publié au plus ancien, puis l'offre sans date.
    # Les écarts sont choisis pour tomber sans ambiguïté de part et d'autre des seuils
    # testés : 1 j < 7 ; 7 < 35 j < 60 ; 60 < 370 j < 1000.
    SPECS = [
        ("Publiée hier", _il_y_a(1)),
        ("Publiée le mois dernier", _il_y_a(35)),
        ("Publiée il y a un an", _il_y_a(370)),
        ("Sans date de publication", None),
    ]

    def seed(db_path) -> dict[str, str | None]:
        conn = storage.connect(db_path)
        try:
            # fetched_date croissant alors que posted_date décroît -> les deux tris divergent.
            for i, (title, posted) in enumerate(SPECS):
                raw = RawJob(
                    source="france_travail",
                    native_id=f"DATED-{i}",
                    title=title,
                    company="Chronos",
                    location="Paris",
                    description=f"Offre « {title} ».",
                    url=f"https://exemple.fr/dated/{i}",
                    posted_date=posted,
                )
                # Récupération d'autant plus RÉCENTE que l'offre est publiée ANCIENNEMENT :
                # c'est cette inversion qui empêche un tri par publication de passer par
                # accident en triant par date de collecte.
                job = raw_to_job(
                    raw,
                    fetched_date=(
                        now - timedelta(hours=len(SPECS) - 1 - i)
                    ).isoformat(),
                )
                storage.upsert_job(conn, storage.LOCAL_USER_ID, job)
        finally:
            conn.close()
        return dict(SPECS)

    return seed


# --- Rendu Typst : dossier d'écriture jetable -------------------------------
@pytest.fixture
def typst_out_dir():
    """Dossier d'écriture pour les rendus Typst, NETTOYÉ après le test.

    Le binaire `typst` (snap) ne peut pas écrire dans /tmp ; on reste donc sous le
    dossier `output/` du projet.
    """
    import shutil

    base = PROJECT_ROOT / "output" / "_pytest"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=base))
    yield d
    shutil.rmtree(d, ignore_errors=True)
