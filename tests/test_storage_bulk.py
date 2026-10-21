"""Écriture GROUPÉE d'une run : mêmes décisions que l'écriture unitaire, sans le N+1.

Deux propriétés, et elles ne se remplacent pas l'une l'autre :

1. ÉQUIVALENCE — `upsert_jobs` doit rendre exactement ce que rendait la boucle
   `upsert_job`, et laisser la base dans le même état. C'est ce qui autorise à remplacer
   l'un par l'autre sans relire toute la règle de dédup.

2. COÛT — le nombre de requêtes ne doit pas croître avec le nombre d'offres. C'est la
   propriété qui a manqué : sur un fichier SQLite local, 13 requêtes par offre passaient
   inaperçues ; sur une base distante à ~150 ms l'aller-retour, une run de 300 offres
   demandait près de dix minutes et l'écran abandonnait avant la fin. Un test qui ne
   mesure QUE le résultat aurait laissé revenir la panne.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, select

from jobtool.aggregator import storage
from jobtool.aggregator.models import RawJob
from jobtool.aggregator.normalize import raw_to_job
from jobtool.core.db import ensure_user, offer_occurrences, offers, user_offers


def _raw(source="adzuna", native_id="n1", title="Poste", url="https://x.fr/1"):
    return RawJob(
        source=source,
        native_id=native_id,
        title=title,
        company="ACME",
        url=url,
        description="Offre de test.",
    )


#: Une run qui exerce les trois filets anti-doublon, entre offres d'une MÊME run.
#:
#: Les 4e et 5e lignes sont les cas retors, ceux qu'un scénario trop sage manque :
#:   - 4e : même entreprise et même intitulé sur une autre source, mais SANS lieu ni lien
#:     commun. Rien ne prouve que c'est la même annonce : par prudence, elle n'est PAS
#:     fusionnée (le dédoublonnage approché exige un lieu commun) ;
#:   - 5e : même lien qu'Adzuna sur une autre source. Ce lien n'identifie pas une annonce
#:     connue (ce n'est pas un lien d'ATS) et l'annonce qui le porte a été RENOMMÉE entre-
#:     temps (3e ligne) : intitulés différents, le lien peut être générique -> pas de
#:     fusion. La fusion par lien est éprouvée dans test_dedup_sources.py.
#: Les deux chemins (unitaire, groupé) doivent décider EXACTEMENT pareil.
SCENARIO = [
    _raw(),
    _raw(),  # doublon exact
    _raw(title="Poste RENOMMÉ"),  # même native_id, texte dérivé -> autre dedup_hash
    _raw(source="francetravail", native_id="f9", url="https://ft.fr/f9"),  # 2e source
    _raw(source="francetravail", native_id="f8"),  # même lien qu'adzuna, autre source
    _raw(native_id="n2", title="Poste B", url="https://x.fr/2"),  # inédite
    _raw(native_id="n3", title="Poste C", url=""),  # sans lien exploitable
    _raw(native_id="n3", title="Poste C", url=""),  # doublon sans lien
]


@pytest.fixture
def base(tmp_path):
    """Base jetable + un compte réel, et un compteur de requêtes SQL."""

    def make():
        conn = storage.connect(tmp_path / f"{uuid.uuid4().hex}.db")
        user_id = uuid.uuid4()
        ensure_user(conn, user_id, email="a@b.fr", name="a")
        conn.commit()
        return conn, user_id

    return make


def _snapshot(conn):
    """État complet des trois tables — ce qui doit être identique des deux côtés."""
    return (
        sorted(r[0] for r in conn.execute(select(offers.c.dedup_hash))),
        sorted(
            tuple(r)
            for r in conn.execute(
                select(
                    offer_occurrences.c.offer_id,
                    offer_occurrences.c.source,
                    offer_occurrences.c.native_id,
                )
            )
        ),
        sorted(r[0] for r in conn.execute(select(user_offers.c.dedup_hash))),
    )


def _count_queries(conn):
    """Compteur de requêtes émises sur ce moteur."""
    counter = {"n": 0}

    @event.listens_for(conn.engine, "before_cursor_execute")
    def _tick(*_args, **_kwargs):
        counter["n"] += 1

    return counter


@pytest.mark.parametrize("passes", [1, 2], ids=["une run", "run rejouée"])
def test_bulk_decides_exactly_like_the_one_by_one_path(base, passes):
    """Mêmes booléens, même base — y compris quand la run est rejouée à l'identique."""
    conn_a, user_a = base()
    conn_b, user_b = base()

    one_by_one, bulk = [], []
    for _ in range(passes):
        jobs = [raw_to_job(r) for r in SCENARIO]
        one_by_one.append([storage.upsert_job(conn_a, user_a, j) for j in jobs])
        bulk.append(storage.upsert_jobs(conn_b, user_b, jobs))

    assert bulk == one_by_one, "les décisions offre par offre sont identiques"
    assert _snapshot(conn_b) == _snapshot(conn_a), "et la base finit dans le même état"

    # Le scénario doit VRAIMENT exercer la dédup, sinon l'égalité ci-dessus ne prouve rien.
    assert one_by_one[0].count(True) == 5, (
        "5 offres entrent, 3 sont des répétitions d'une occurrence connue"
    )

    # …et chaque occurrence garde sa source : rien n'est perdu de la provenance.
    _, traces, _ = _snapshot(conn_b)
    sources = sorted({src for _hash, src, _native in traces})
    assert sources == ["adzuna", "francetravail"], (
        f"les deux sources de l'annonce sont conservées : {sources}"
    )


def test_query_count_does_not_grow_with_the_run(base):
    """Le coût d'une run ne suit pas son volume : c'est CE qui rendait la recherche infinie.

    Le seuil est volontairement large — on ne verrouille pas un chiffre exact, on interdit
    le retour d'un coût proportionnel au nombre d'offres.
    """
    conn, user_id = base()
    counter = _count_queries(conn)

    jobs = [
        raw_to_job(_raw(native_id=f"n{i}", title=f"Poste {i}", url=f"https://x.fr/{i}"))
        for i in range(200)
    ]
    storage.upsert_jobs(conn, user_id, jobs)

    assert counter["n"] <= 20, (
        f"200 offres en {counter['n']} requêtes : le chemin unitaire en demandait 2600, "
        "soit plus de six minutes sur une base distante"
    )
