"""Score de compatibilité (aggregator.matching) — calcul pur, déterministe.

Ce que la page « Découvrir » promet à l'écran, et donc ce qui est protégé ici :
  - le score reflète les critères (intitulé > description > rien) ;
  - un critère non renseigné ne pèse pas, et ne fait pas plafonner le score ;
  - l'affichage reste crédible : jamais 100 %, jamais 0 %, arrondi cohérent ;
  - l'ordre de la pile est total et reproductible (sinon le préchargement dédouble).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobtool.aggregator.matching import (
    MAX_PERCENT,
    MIN_PERCENT,
    MatchCriteria,
    contract_kinds,
    department_of,
    keyword_tokens,
    rank_jobs,
    score_job,
    to_percent,
)
from jobtool.aggregator.models import Job

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _job(
    ident: str = "a",
    *,
    title: str = "Poste",
    description: str = "",
    location: str = "",
    contract_type: str = "",
    remote: str = "inconnu",
    age_days: int | None = 0,
) -> Job:
    posted = None if age_days is None else (NOW - timedelta(days=age_days)).isoformat()
    return Job(
        id=ident,
        source="test",
        native_id=ident,
        title=title,
        company="ACME",
        location=location,
        remote=remote,  # type: ignore[arg-type]
        contract_type=contract_type,
        description=description,
        url=f"https://exemple.fr/{ident}",
        posted_date=posted,
        fetched_date=NOW.isoformat(),
        dedup_hash=ident,
    )


def _pct(job: Job, **criteria) -> int:
    return score_job(job, MatchCriteria(**criteria), now=NOW).percent


# --- Mots-clés ----------------------------------------------------------------


def test_keyword_in_title_beats_description_beats_absent():
    kw = {"keywords": "data scientist"}
    in_title = _pct(_job(title="Data Scientist H/F"), **kw)
    in_desc = _pct(_job(title="Analyste", description="Équipe data scientist"), **kw)
    absent = _pct(_job(title="Comptable", description="Paie et bilans"), **kw)
    assert in_title > in_desc > absent


def test_keywords_ignore_case_accents_and_stopwords():
    assert keyword_tokens("Chef de PROJET  Développement") == [
        "chef",
        "projet",
        "developpement",
    ]
    assert _pct(
        _job(title="chef de projet developpement"), keywords="Chef Projet Développement"
    ) == _pct(
        _job(title="CHEF DE PROJET DÉVELOPPEMENT"), keywords="chef projet developpement"
    )


def test_keyword_matches_word_start_not_inside_word():
    """« data » trouve « Data » et « datascience », pas « metadata »."""
    kw = {"keywords": "data"}
    assert _pct(_job(title="Datascience junior"), **kw) == _pct(
        _job(title="Data junior"), **kw
    )
    assert _pct(_job(title="Metadata manager"), **kw) < _pct(
        _job(title="Data junior"), **kw
    )


def test_partial_keyword_match_is_proportional():
    kw = {"keywords": "python django kubernetes"}
    one = _pct(_job(title="Développeur Python"), **kw)
    two = _pct(_job(title="Développeur Python Django"), **kw)
    three = _pct(_job(title="Python Django Kubernetes"), **kw)
    assert one < two < three


# --- Critères non renseignés, bornes et arrondis ------------------------------


def test_missing_criteria_do_not_cap_the_score():
    """Sans lieu ni contrat, une offre parfaite sur les mots-clés et du jour atteint le
    plafond — et non 60 % parce que 40 points « manqueraient »."""
    assert (
        _pct(
            _job(title="Data engineer", description="data engineer"),
            keywords="data engineer",
        )
        == MAX_PERCENT
    )


def test_never_100_never_0():
    perfect = _job(
        title="CDI Data engineer",
        description="data engineer",
        location="75 - Paris",
        contract_type="CDI",
    )
    assert (
        _pct(perfect, keywords="data engineer", departments=("75",), contracts=("cdi",))
        == 99
    )
    hopeless = _job(
        title="Boulanger",
        location="13 - Marseille",
        contract_type="CDD - 6 Mois",
        age_days=3650,
    )
    assert (
        _pct(hopeless, keywords="data", departments=("75",), contracts=("cdi",))
        == MIN_PERCENT
    )


@pytest.mark.parametrize(
    ("points", "expected"),
    [(0.0, 1), (0.4, 1), (1.5, 2), (49.49, 49), (49.5, 50), (98.6, 99), (100.0, 99)],
)
def test_percent_rounding_and_bounds(points, expected):
    assert to_percent(points) == expected


def test_criteria_breakdown_sums_to_score():
    job = _job(
        title="Data analyst", location="92 - Nanterre", contract_type="CDI", age_days=7
    )
    result = score_job(
        job,
        MatchCriteria(keywords="data analyst", departments=("75",), contracts=("cdi",)),
        now=NOW,
    )
    assert pytest.approx(sum(c.weight for c in result.criteria)) == 100
    assert result.percent == to_percent(sum(c.points for c in result.criteria))
    assert [c.key for c in result.criteria] == [
        "keywords",
        "location",
        "contract",
        "freshness",
    ]


def test_no_criteria_gives_a_neutral_score():
    """Sans critère de pertinence, la fraîcheur seule ne peut pas afficher « 91 % » :
    la pertinence est neutre (0,5 × 85) et le score reste dans une zone moyenne."""
    result = score_job(_job(age_days=14), MatchCriteria(), now=NOW)
    assert [c.key for c in result.criteria] == ["neutral", "freshness"]
    assert result.percent == 50  # 42,5 + 0,5 × 15 (demi-vie : 14 jours = moitié)
    fresh = score_job(_job(age_days=0), MatchCriteria(), now=NOW).percent
    assert 50 < fresh <= 58


def test_score_is_deterministic():
    job = _job(title="Data engineer", location="75 - Paris", age_days=3)
    crit = MatchCriteria(keywords="data", departments=("75",), contracts=("cdi",))
    assert score_job(job, crit, now=NOW) == score_job(job, crit, now=NOW)


# --- Lieu -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("location", "dept"),
    [
        ("75 - Paris 14e Arrondissement", "75"),
        ("2A - Ajaccio", "2A"),
        ("974 - Saint-Denis", "974"),
        ("69003 Lyon", "69"),
        ("Lyon (69)", "69"),
        ("97400 Saint-Denis", "974"),
        ("Paris", None),
        ("", None),
    ],
)
def test_department_of(location, dept):
    assert department_of(location) == dept


def test_location_scoring():
    crit = {"departments": ("75", "92")}
    in_dept = _pct(_job(location="92 - Nanterre"), **crit)
    remote = _pct(_job(location="13 - Marseille", remote="oui"), **crit)
    unknown = _pct(_job(location="Île-de-France"), **crit)
    elsewhere = _pct(_job(location="13 - Marseille"), **crit)
    assert in_dept > remote > unknown > elsewhere


# --- Contrat -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contract_type", "title", "kinds"),
    [
        ("CDI", "Développeur", {"cdi"}),
        ("CDD - 12 Mois", "Développeur", {"cdd"}),
        ("Intérim - 3 Mois", "Cariste", {"interim"}),
        ("Profession libérale", "Kiné", {"freelance"}),
        ("", "Stage - Data analyst", {"stage"}),
        ("", "Alternance développeur web", {"alternance"}),
        ("", "Apprenti(e) comptable", {"alternance"}),
        ("permanent full_time", "Engineer", {"cdi"}),
        ("contract", "Engineer", {"cdd"}),
        # Vocabulaire anglais lu dans le seul champ contrat, jamais dans l'intitulé.
        ("", "Contract manager", set()),
        ("", "Ingénieur", set()),
    ],
)
def test_contract_kinds(contract_type, title, kinds):
    assert contract_kinds(_job(title=title, contract_type=contract_type)) == kinds


def test_contract_scoring():
    crit = {"contracts": ("stage", "alternance")}
    match = _pct(_job(title="Stage marketing"), **crit)
    unknown = _pct(_job(title="Marketing"), **crit)
    other = _pct(_job(title="Marketing", contract_type="CDI"), **crit)
    assert match > unknown > other


# --- Fraîcheur -----------------------------------------------------------------


def test_freshness_decreases_with_age_and_unknown_is_penalised():
    fresh = _pct(_job(age_days=0))
    week = _pct(_job(age_days=7))
    month = _pct(_job(age_days=30))
    unknown = _pct(_job(age_days=None))
    assert fresh > week > month
    assert unknown == 47  # 42,5 (pertinence neutre) + 0,3 × 15


def test_future_date_counts_as_today():
    assert _pct(_job(age_days=-3)) == _pct(_job(age_days=0))


# --- Classement ----------------------------------------------------------------


def test_rank_orders_by_score_then_date_then_id():
    jobs = [
        _job("c", title="Comptable", age_days=0),
        _job("b", title="Data engineer", age_days=2),
        _job("a", title="Data engineer", age_days=2),
        _job("d", title="Data engineer", age_days=0),
    ]
    ranked = rank_jobs(
        jobs, MatchCriteria(keywords="data engineer"), now=NOW, min_percent=1
    )
    assert [r.job.dedup_hash for r in ranked] == ["d", "a", "b", "c"]
    assert [r.percent for r in ranked] == sorted(
        (r.percent for r in ranked), reverse=True
    )


def test_rank_applies_threshold_and_exclusions():
    jobs = [
        _job("x", title="Data engineer"),
        _job("y", title="Data analyst"),
        _job("z", title="Boulanger", age_days=400),
    ]
    crit = MatchCriteria(keywords="data")
    ids = [r.job.dedup_hash for r in rank_jobs(jobs, crit, now=NOW, min_percent=25)]
    assert "z" not in ids  # sous le seuil
    ids = [r.job.dedup_hash for r in rank_jobs(jobs, crit, now=NOW, exclude=["x"])]
    assert ids == ["y"]
