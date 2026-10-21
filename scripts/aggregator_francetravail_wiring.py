"""Preuve SANS réseau du connecteur France Travail (Phase 1).

On STUBE la couche HTTP (objet `requests.Session` factice : `.post` pour le token, `.get`
pour la recherche) — AUCUN vrai réseau, AUCUN secret réel. On prouve :
  - le token est demandé UNE fois puis réutilisé (cache) sur plusieurs pages ;
  - HTTP 206 est traité comme un SUCCÈS (la pagination fonctionne) ;
  - le throttling ≤ 4 req/s est respecté (RateLimiter à horloge/sleep mockés -> compteur) ;
  - mapping JSON France Travail -> RawJob -> Job correct ;
  - MINIMISATION : les coordonnées de contact ne sont JAMAIS stockées dans `raw` ;
  - ISOLATION : une page en erreur n'interrompt pas la run -> résultats PARTIELS renvoyés.

Usage : python3 scripts/aggregator_francetravail_wiring.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jobtool.aggregator.francetravail.auth import FranceTravailAuth
from jobtool.aggregator.francetravail.connector import (
    FranceTravailConnector,
    RateLimiter,
)
from jobtool.aggregator.models import SearchCriteria
from jobtool.aggregator.normalize import raw_to_job

TOKEN_PAYLOAD = {"access_token": "FAKE-TOKEN-xyz", "expires_in": 1500, "token_type": "Bearer"}

# Offres FT factices. NB : `contact` est présent -> doit être ÉCARTÉ du raw (minimisation).
OFFER1 = {
    "id": "187XML",
    "intitule": "Ingénieur Calcul Haute Performance (H/F)",
    "entreprise": {"nom": "Helix Compute"},
    "lieuTravail": {"libelle": "38 - Grenoble", "commune": "38185"},
    "typeContrat": "MIS",
    "typeContratLibelle": "Mission intérimaire",
    "description": "Optimisation et portage de noyaux de calcul sur GPU et MPI.",
    "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/detail/187XML"},
    "dateCreation": "2026-06-10T09:00:00.000Z",
    "contact": {"nom": "Mme Dupont", "courriel": "recrutement-secret@example.com"},
}
OFFER2 = {
    "id": "188YQZ",
    "intitule": "Data Engineer",
    "entreprise": {"nom": "Globex"},
    "lieuTravail": {"libelle": "75 - Paris", "commune": "75056"},
    "typeContratLibelle": "CDI",
    "description": "Pipelines de données et orchestration.",
    "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/detail/188YQZ"},
    "dateCreation": "2026-06-11T10:00:00.000Z",
    "contact": {"courriel": "ne-pas-stocker@example.com"},
}


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


class FakeSession:
    """Session requests factice : compte les appels, programme les réponses GET."""

    def __init__(self, get_outcomes: list):
        self.post_calls = 0
        self.get_calls = 0
        self._get_outcomes = list(get_outcomes)
        self.last_get_headers: dict | None = None
        self.last_get_params: dict | None = None

    def post(self, url, data=None, headers=None, timeout=None):
        self.post_calls += 1
        # Garde-fou de test : les identifiants DOIVENT être transmis (mais on ne les logge pas).
        assert data and data.get("grant_type") == "client_credentials"
        return FakeResponse(200, TOKEN_PAYLOAD)

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls += 1
        self.last_get_headers = headers or {}
        self.last_get_params = dict(params or {})
        outcome = self._get_outcomes[min(self.get_calls - 1, len(self._get_outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        status, data = outcome
        return FakeResponse(status, data)


def _make_rate_limiter():
    """RateLimiter à horloge FIGÉE (0.0) + sleep compté : prouve le throttling sans attente."""
    sleeps: list[float] = []
    rl = RateLimiter(
        max_per_second=4, clock=lambda: 0.0, sleep=lambda s: sleeps.append(s)
    )
    return rl, sleeps


def main() -> None:
    failures: list[str] = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)
            print(f"  ✗ ATTENDU : {msg}")
        else:
            print(f"  ✓ {msg}")

    # ===================== SCÉNARIO 1 : happy path (206 + pagination) =========
    print("\n=== Scénario 1 — 206, pagination, token caché, throttling, mapping ===")
    # page_size=2 : page1 PLEINE (2 résultats, 206) -> continue ; page2 vide (206) -> stop.
    session = FakeSession([
        (206, {"resultats": [OFFER1, OFFER2], "filtresPossibles": []}),
        (206, {"resultats": [], "filtresPossibles": []}),
    ])
    rl, sleeps = _make_rate_limiter()
    auth = FranceTravailAuth("FAKE_ID", "FAKE_SECRET", "api_offresdemploiv2 o2dsoffre", session=session)
    connector = FranceTravailConnector(auth, session=session, page_size=2, rate_limiter=rl)

    raws = connector.fetch(SearchCriteria(mots_cles="hpc", commune="38185", rayon=30, max_results=10))

    expect(len(raws) == 2, f"206 traité comme succès : 2 offres collectées (vu {len(raws)})")
    expect(session.post_calls == 1, f"token demandé UNE seule fois puis réutilisé (cache) — POST={session.post_calls}")
    expect(session.get_calls == 2, f"pagination : 2 pages interrogées — GET={session.get_calls}")
    expect(len(sleeps) >= 1, f"throttling engagé entre requêtes (sleeps={len(sleeps)}, ≤4 req/s)")
    expect(connector.errors == [], "aucune erreur sur le happy path")
    # En-tête : Authorization Bearer + User-Agent explicite (sans exposer de secret réel).
    hdr = session.last_get_headers or {}
    expect(hdr.get("Authorization") == "Bearer FAKE-TOKEN-xyz", "en-tête Authorization Bearer posé")
    expect("User-Agent" in hdr, "User-Agent explicite envoyé")

    # Mapping JSON -> RawJob
    r0 = raws[0]
    expect(r0.source == "france_travail", "source = france_travail")
    expect(r0.native_id == "187XML", "native_id <- offer.id")
    expect(r0.title == "Ingénieur Calcul Haute Performance (H/F)", "title <- intitule")
    expect(r0.company == "Helix Compute", "company <- entreprise.nom")
    expect(r0.location == "38 - Grenoble", "location <- lieuTravail.libelle")
    expect(r0.contract_type == "Mission intérimaire", "contract_type <- typeContratLibelle")
    expect(r0.url == "https://candidat.francetravail.fr/offres/detail/187XML", "url <- origineOffre.urlOrigine")

    # Mapping complet -> Job + MINIMISATION (contact jamais stocké)
    job0 = raw_to_job(r0)
    raw_blob = json.dumps(job0.raw, ensure_ascii=False)
    expect("contact" not in job0.raw, "MINIMISATION : aucune clé 'contact' dans raw")
    expect("recrutement-secret@example.com" not in raw_blob, "MINIMISATION : email de contact absent du raw")
    expect(job0.raw.get("id") == "187XML" and job0.raw.get("intitule"), "raw conserve les champs utiles (liste blanche)")

    # ===================== SCÉNARIO 2 : isolation (erreur réseau page 2) ======
    print("\n=== Scénario 2 — isolation : erreur sur une page -> partiel renvoyé ===")
    session2 = FakeSession([
        (206, {"resultats": [OFFER1, OFFER2], "filtresPossibles": []}),
        requests.ConnectionError("réseau coupé page 2"),  # la run ne doit PAS planter
    ])
    rl2, _ = _make_rate_limiter()
    auth2 = FranceTravailAuth("FAKE_ID", "FAKE_SECRET", "scope", session=session2)
    connector2 = FranceTravailConnector(auth2, session=session2, page_size=2, rate_limiter=rl2)

    raws2 = connector2.fetch(SearchCriteria(mots_cles="data", max_results=10))
    expect(len(raws2) == 2, "résultats PARTIELS renvoyés malgré l'erreur page 2")
    expect(len(connector2.errors) == 1, "l'erreur réseau est CAPTURÉE (notée, pas levée)")
    expect("réseau" in connector2.errors[0].lower(), f"erreur lisible : {connector2.errors[0]!r}")

    # ===================== SCÉNARIO 3 : statut HTTP non succès (ex. 500) =======
    print("\n=== Scénario 3 — HTTP 500 -> succès partiel, pas de crash ===")
    session3 = FakeSession([
        (206, {"resultats": [OFFER1, OFFER2], "filtresPossibles": []}),
        (500, {}),
    ])
    rl3, _ = _make_rate_limiter()
    auth3 = FranceTravailAuth("FAKE_ID", "FAKE_SECRET", "scope", session=session3)
    connector3 = FranceTravailConnector(auth3, session=session3, page_size=2, rate_limiter=rl3)
    raws3 = connector3.fetch(SearchCriteria(max_results=10))
    expect(len(raws3) == 2, "500 sur page 2 -> on garde les 2 de la page 1")
    expect(any("500" in e for e in connector3.errors), "HTTP 500 noté comme erreur non fatale")

    # ===================== SCÉNARIO 4 : 400 -> corps remonté, sans secret =====
    print("\n=== Scénario 4 — HTTP 400 : corps de l'API remonté (diagnostic) ===")
    body_400 = {"message": "Le paramètre rayon nécessite le paramètre commune valide"}
    session4 = FakeSession([(400, body_400)])
    rl4, _ = _make_rate_limiter()
    auth4 = FranceTravailAuth("FAKE_ID", "SUPER_SECRET_VALUE", "scope", session=session4)
    # Mode CONCIS (debug=False) : extrait du corps quand même présent.
    connector4 = FranceTravailConnector(auth4, session=session4, page_size=20, rate_limiter=rl4)
    raws4 = connector4.fetch(SearchCriteria(mots_cles="data scientist", commune="75056", rayon=30, max_results=20))

    expect(raws4 == [], "400 dès la 1ʳᵉ page -> 0 offre (succès partiel vide)")
    expect(len(connector4.errors) == 1, "une erreur non fatale notée (isolation préservée, pas de crash)")
    err = connector4.errors[0]
    expect("400" in err, "le message contient le code HTTP 400")
    expect("Le paramètre rayon nécessite le paramètre commune valide" in err,
           "le message REMONTE le corps JSON de l'API (le paramètre fautif)")
    expect("/offres/search" in err, "le message contient l'URL appelée")
    expect("commune" in err and "rayon" in err and "range" in err,
           "le message contient les PARAMS envoyés (commune, rayon, range)")
    # Aucun secret : ni client_secret, ni token, ni Authorization.
    expect("SUPER_SECRET_VALUE" not in err, "AUCUN secret (client_secret) dans le message")
    expect("FAKE-TOKEN-xyz" not in err and "Bearer" not in err, "AUCUN token / Authorization dans le message")
    print(f"  [concis] {err}")

    # Mode DEBUG : même corps, non tronqué (ici corps court, on vérifie surtout l'absence de secret).
    session4b = FakeSession([(400, body_400)])
    rl4b, _ = _make_rate_limiter()
    auth4b = FranceTravailAuth("FAKE_ID", "SUPER_SECRET_VALUE", "scope", session=session4b)
    connector4b = FranceTravailConnector(auth4b, session=session4b, page_size=20, rate_limiter=rl4b, debug=True)
    connector4b.fetch(SearchCriteria(mots_cles="data scientist", commune="75056", rayon=30, max_results=20))
    err_dbg = connector4b.errors[0]
    expect("Le paramètre rayon nécessite le paramètre commune valide" in err_dbg,
           "mode debug : corps complet présent")
    expect("SUPER_SECRET_VALUE" not in err_dbg and "Bearer" not in err_dbg,
           "mode debug : toujours AUCUN secret")

    # ===================== SCÉNARIO 5 : 400 corps LONG -> tronqué en concis ====
    print("\n=== Scénario 5 — corps long : tronqué en concis, complet en debug ===")
    long_body = {"message": "X" * 800}
    s5 = FakeSession([(400, long_body)])
    rl5, _ = _make_rate_limiter()
    a5 = FranceTravailAuth("ID", "SECRET", "scope", session=s5)
    c5_concise = FranceTravailConnector(a5, session=s5, page_size=20, rate_limiter=rl5)
    c5_concise.fetch(SearchCriteria(mots_cles="x", max_results=20))
    s5b = FakeSession([(400, long_body)])
    rl5b, _ = _make_rate_limiter()
    a5b = FranceTravailAuth("ID", "SECRET", "scope", session=s5b)
    c5_debug = FranceTravailConnector(a5b, session=s5b, page_size=20, rate_limiter=rl5b, debug=True)
    c5_debug.fetch(SearchCriteria(mots_cles="x", max_results=20))
    expect(len(c5_concise.errors[0]) < len(c5_debug.errors[0]),
           "concis plus court que debug (corps long tronqué en concis)")
    expect("--verbose" in c5_concise.errors[0], "concis invite à --verbose pour le corps complet")

    # ===================== SCÉNARIO 6 : filtres géographiques exclusifs =======
    print("\n=== Scénario 6 — géo : departement / commune / les deux / aucun ===")

    def _params_sent_for(criteria: SearchCriteria):
        """Lance un fetch stubé (206 vide) et renvoie les params réellement envoyés à l'API."""
        sess = FakeSession([(206, {"resultats": [], "filtresPossibles": []})])
        rl, _ = _make_rate_limiter()
        a = FranceTravailAuth("ID", "SECRET", "scope", session=sess)
        c = FranceTravailConnector(a, session=sess, page_size=20, rate_limiter=rl)
        raws = c.fetch(criteria)
        return sess.last_get_params or {}, raws, c

    # (a) departement seul -> departement présent, PAS de commune/rayon.
    p_dep, raws_dep, c_dep = _params_sent_for(
        SearchCriteria(mots_cles="data scientist", departement="75,92,93,94", max_results=20)
    )
    expect(p_dep.get("departement") == "75,92,93,94", "departement seul : envoyé tel quel (virgule conservée)")
    expect("commune" not in p_dep and "rayon" not in p_dep, "departement seul : ni commune ni rayon envoyés")
    expect(raws_dep == [] and c_dep.errors == [], "departement seul : 206 vide traité comme succès")

    # (b) commune seule -> commune (+rayon), PAS de departement.
    p_com, _, _ = _params_sent_for(
        SearchCriteria(mots_cles="x", commune="92012", rayon=30, max_results=20)
    )
    expect(p_com.get("commune") == "92012" and p_com.get("rayon") == 30, "commune seule : commune+rayon envoyés (inchangé)")
    expect("departement" not in p_com, "commune seule : pas de departement envoyé")

    # (c) commune ET departement -> departement prioritaire, commune absente, AVERTISSEMENT.
    crit_both = SearchCriteria(mots_cles="x", commune="92012", rayon=30, departement="75", max_results=20)
    geo_both, warn_both = crit_both.resolve_geo()
    p_both, _, _ = _params_sent_for(crit_both)
    expect(p_both.get("departement") == "75", "les deux : departement gagne (envoyé)")
    expect("commune" not in p_both and "rayon" not in p_both, "les deux : commune/rayon ABSENTS des params")
    expect(warn_both is not None and "departement prioritaire" in warn_both, f"les deux : avertissement émis ({warn_both!r})")

    # (d) aucun -> recherche nationale (ni commune ni departement).
    p_none, _, _ = _params_sent_for(SearchCriteria(mots_cles="x", max_results=20))
    expect("commune" not in p_none and "departement" not in p_none and "rayon" not in p_none,
           "aucun filtre géo : recherche nationale (params sans géo)")

    print("\n" + "=" * 64)
    if failures:
        print(f"ÉCHEC : {len(failures)} assertion(s) non satisfaite(s) :")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("TOUTES LES PREUVES PHASE 1 PASSENT (token caché+réutilisé, 206 géré, throttling, "
          "mapping, minimisation contact, isolation, corps d'erreur 400 sans secret, "
          "+ filtres géo exclusifs departement/commune) — SANS réseau.")


if __name__ == "__main__":
    main()
