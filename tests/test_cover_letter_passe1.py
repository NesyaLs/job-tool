"""Preuve SANS clé API de la PASSE 1 de la lettre (routage + vérifs + boucle).

Stub du client (patch de `call_structured` dans la boucle de correction et de `make_client`
dans passe1). Le stub renvoie des CoverLetterRoutingOutput fabriqués pour prouver le
COMPORTEMENT des vérifs et la convergence de la boucle. ids de tests/fixtures/profil_demo.yaml.
"""

from __future__ import annotations

from unittest.mock import patch

from jobtool.documents.cover_letter import passe1
from jobtool.documents.cover_letter.schema import CoverLetterRoutingOutput


def run_case(base, outputs, max_retries=2, capture=None):
    """Exécute run_passe1 avec un client stubé renvoyant `outputs` (le dernier répété au-delà).

    Retourne (output, issues, attempts). Si `capture` (dict) est fourni, enregistre sous
    "calls" le texte complet des messages transmis au LLM à chaque appel.
    """
    seq = list(outputs)
    counter = {"i": 0}

    def fake_call_structured(client, messages, response_format, **kwargs):
        if capture is not None:
            capture.setdefault("calls", []).append(
                "\n".join(m.get("content", "") for m in messages)
            )
        out = seq[min(counter["i"], len(seq) - 1)]
        counter["i"] += 1
        return out

    attempts: list[tuple[int, list]] = []

    def on_attempt(attempt, parsed, issues):
        attempts.append((attempt, list(issues)))

    with (
        patch(
            "jobtool.core.llm.correction_loop.call_structured",
            side_effect=fake_call_structured,
        ),
        patch(
            "jobtool.documents.cover_letter.passe1.make_client",
            side_effect=lambda: object(),
        ),
    ):
        output, issues = passe1.run_passe1(
            base, "OFFRE STUB", max_retries=max_retries, on_attempt=on_attempt
        )
    return output, issues, attempts


def test_generique_clean(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="Acme Corp",
                domaine="data engineering",
                mode="generique",
                projets_ids=[],
                interets_ids=[],
            )
        ],
    )
    assert issues == [], "générique propre accepté (0 violation)"
    assert len(attempts) == 1, "accepté en 1 tentative"


def test_cible_clean(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=["projet-noyaux-hpc.1"],
                interets_ids=["Contributions à des logiciels open source"],
            )
        ],
    )
    assert issues == [], "ciblé propre (ids existants) accepté"
    assert len(attempts) == 1, "accepté en 1 tentative"


def test_invented_id_corrected_by_loop(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=["projet-INEXISTANT.9"],
                interets_ids=[],
            ),
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=["projet-noyaux-hpc.1"],
                interets_ids=[],
            ),
        ],
    )
    assert len(attempts) >= 2, (
        "la boucle a redemandé après la violation (>= 2 tentatives)"
    )
    assert any(v.kind == "unknown_project_achievement" for v in attempts[0][1]), (
        "tentative 1 : violation de traçabilité (id inconnu)"
    )
    assert issues == [], "tentative 2 (id réel) : acceptée"
    assert out.projets_ids == ["projet-noyaux-hpc.1"], (
        "résultat final porte l'id réel corrigé"
    )


def test_mode_cible_without_material(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="Acme",
                domaine="data",
                mode="cible",
                projets_ids=[],
                interets_ids=[],
            )
        ],
    )
    assert any(v.kind == "incoherent_mode" for v in issues), (
        "cible sans id => violation de cohérence du mode"
    )


def test_mode_generique_with_id(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="Acme",
                domaine="data",
                mode="generique",
                projets_ids=["projet-noyaux-hpc.1"],
                interets_ids=[],
            )
        ],
    )
    assert any(v.kind == "incoherent_mode" for v in issues), (
        "generique avec id => violation de cohérence du mode"
    )


def test_two_distinct_projects_then_converge(moi_base):
    # tentative 1 = 2 projets distincts -> violation ; tentative 2 = 2 achievements du MÊME projet -> accepté.
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=["projet-noyaux-hpc.1", "projet-filtres-gpu.1"],
                interets_ids=[],
            ),
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=["projet-noyaux-hpc.1", "projet-noyaux-hpc.2"],
                interets_ids=[],
            ),
        ],
    )
    assert len(attempts) >= 2, "la boucle a redemandé après la violation"
    assert any(v.kind == "too_many_projects" for v in attempts[0][1]), (
        "tentative 1 : « trop de projets distincts » (2 > 1)"
    )
    assert issues == [], "tentative 2 (2 achievements d'1 projet) : acceptée"
    assert out.projets_ids == ["projet-noyaux-hpc.1", "projet-noyaux-hpc.2"], (
        "résultat : 2 achievements du projet unique"
    )


def test_three_achievements_one_project_accepted(moi_base):
    # La borne plafonne les PROJETS distincts, pas le nombre d'achievements : 3 ids d'un projet = VALIDE.
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="HPC Systems",
                domaine="calcul haute performance",
                mode="cible",
                projets_ids=[
                    "projet-noyaux-hpc.1",
                    "projet-noyaux-hpc.2",
                    "projet-noyaux-hpc.3",
                ],
                interets_ids=[],
            )
        ],
    )
    assert issues == [], "3 achievements d'1 seul projet acceptés (0 violation)"
    assert len(attempts) == 1, "accepté en 1 tentative"
    assert not any(v.kind == "too_many_projects" for v in issues), (
        "aucune violation « trop de projets distincts »"
    )


def test_bare_experience_id_enriched_message(moi_base):
    # Bug f_o_7 : id d'EXPÉRIENCE nu (sans .N) rejeté ; message de correction enrichi ; .N accepté.
    cap: dict = {}
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="LCL",
                domaine="vision par ordinateur",
                mode="cible",
                projets_ids=["projet-noyaux-hpc"],
                interets_ids=[],
            ),
            CoverLetterRoutingOutput(
                entreprise="LCL",
                domaine="vision par ordinateur",
                mode="cible",
                projets_ids=["projet-noyaux-hpc.1"],
                interets_ids=[],
            ),
        ],
        capture=cap,
    )
    assert any(v.kind == "unknown_project_achievement" for v in attempts[0][1]), (
        "tentative 1 : id nu => violation (borne STRICTE)"
    )
    assert issues == [], "tentative 2 (.N) : acceptée"
    assert "id expérience" not in cap["calls"][0], (
        "contexte (appel 1) : l'id d'expérience nu n'est plus affiché"
    )
    correction = cap["calls"][1]
    assert "EXPÉRIENCE" in correction and "projet-xxx.N" in correction, (
        "correction : nomme la distinction expérience vs achievement"
    )
    assert (
        "Ids d'achievement de PROJET valides" in correction
        and "projet-noyaux-hpc.1" in correction
    ), "correction : re-liste les ids valides"


def test_format_multiline_domain(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="Acme",
                domaine="data\nengineering",
                mode="generique",
                projets_ids=[],
                interets_ids=[],
            )
        ],
    )
    assert any(v.kind == "format_multiligne" for v in issues), (
        "domaine multi-ligne => violation d'hygiène de format"
    )


def test_format_too_long_company(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        [
            CoverLetterRoutingOutput(
                entreprise="X" * 200,
                domaine="data",
                mode="generique",
                projets_ids=[],
                interets_ids=[],
            )
        ],
    )
    assert any(v.kind == "format_trop_long" for v in issues), (
        "entreprise trop longue => violation d'hygiène de format"
    )
