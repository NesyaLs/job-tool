"""Point 2bis de l'audit : le domaine et le noyau n'écrivent RIEN de leur propre chef.

Ces modules sont appelés par l'API : un `print()` y polluerait le stdout d'uvicorn à chaque
génération. La progression passe désormais par des callbacks optionnels (no-op par défaut) ;
la mise en forme appartient à la couche d'interface, qui les rebranche (CLI) ou non (API).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jobtool.core.llm import client

SRC = Path(__file__).resolve().parents[1] / "src" / "jobtool"
SILENT_PACKAGES = ["documents", "core"]


def _print_calls(path: Path) -> list[int]:
    """Numéros de ligne des appels à `print` dans un fichier (analyse syntaxique, pas grep)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


@pytest.mark.parametrize("package", SILENT_PACKAGES)
def test_no_print_in_domain_or_core(package):
    """AUCUN print() dans documents/ ni core/ — la présentation vit dans cli/ et api/."""
    offenders = {
        str(p.relative_to(SRC)): lines
        for p in sorted((SRC / package).rglob("*.py"))
        if (lines := _print_calls(p))
    }
    assert offenders == {}, f"print() résiduel(s) dans {package}/ : {offenders}"


def test_interface_layers_may_print():
    """Contre-preuve : la CLI, elle, imprime bien — le test ci-dessus ne passe pas à vide."""
    printing = [p.name for p in sorted((SRC / "cli").rglob("*.py")) if _print_calls(p)]
    assert printing, "la couche CLI doit rester la voix de l'outil"


# --- Le retry 429 est silencieux par défaut, notifiable sur demande ----------


class _RateLimited(Exception):
    """Exception portant un status 429, telle que `_is_rate_limited` la reconnaît."""

    status_code = 429


def _flaky(failures: int):
    """Appel qui échoue `failures` fois en 429, puis réussit."""
    state = {"n": 0}

    def call():
        if state["n"] < failures:
            state["n"] += 1
            raise _RateLimited("rate_limited")
        return "ok"

    return call


def test_retry_is_silent_by_default(monkeypatch, capsys):
    """Sans callback : aucune sortie (c'est le cas de l'API -> stdout d'uvicorn propre)."""
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    result, retries = client._retry_on_rate_limit(_flaky(2))
    assert (result, retries) == ("ok", 2)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "", (
        f"le transport a écrit : {captured.out!r} / {captured.err!r}"
    )


def test_retry_notifies_callback_with_data(monkeypatch):
    """Avec callback : on reçoit des DONNÉES (n° d'essai, délai, plafond), pas une phrase."""
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    seen: list[tuple] = []
    client._retry_on_rate_limit(_flaky(2), lambda n, d, mx: seen.append((n, d, mx)))
    assert [n for n, _d, _mx in seen] == [1, 2]
    assert all(d > 0 for _n, d, _mx in seen), "un délai de back-off est transmis"
    assert {mx for _n, _d, mx in seen} == {client.MAX_RETRIES_429}


def test_non_429_is_not_retried(monkeypatch):
    """Comportement métier inchangé : une erreur dure n'est ni retentée ni annoncée."""
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    seen: list[tuple] = []

    def boom():
        raise ValueError("panne dure")

    with pytest.raises(ValueError):
        client._retry_on_rate_limit(boom, lambda *a: seen.append(a))
    assert seen == []


# --- Non-régression CLI : l'affichage de progression est bien rebranché -----


def test_cli_cv_still_prints_progress(monkeypatch, tmp_path):
    """`jobtool cv` affiche TOUJOURS sa progression : il fournit les callbacks au domaine.

    On remplace la génération par un double qui DÉCLENCHE les callbacks reçus : ce qui est
    prouvé, c'est le câblage CLI -> domaine et le texte produit par la CLI, sans appel LLM.
    """
    from typer.testing import CliRunner

    from jobtool.cli.main import app
    from jobtool.documents.cv.fill import FillReport
    from jobtool.services import generation_service

    def fake_generate(base, job_offer, **kw):
        kw["on_attempt"]("A", 1, ["v1", "v2"], ["b1"])
        kw["on_attempt"]("B", 2, [], ["b1", "b2"])
        kw["on_retry"](1, 2.5, 5)
        kw["on_progress"](FillReport(renders=3, final_entries=4, final_bullets=9), None)
        raise RuntimeError("stop avant le rendu Typst")  # on ne teste pas le rendu ici

    monkeypatch.setattr(
        generation_service, "generate_tailored_cv_checked", fake_generate
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "clé-factice")
    # Profil fictif : data/moi.yaml est une donnée personnelle, absente d'un clone.
    from jobtool.cli import cv as cli_cv

    monkeypatch.setattr(
        cli_cv, "BASE_PATH", Path(__file__).parent / "fixtures" / "profil_demo.yaml"
    )
    offre = tmp_path / "offre.txt"
    offre.write_text("Une offre.", encoding="utf-8")

    # CliRunner capture lui-même stdout : la sortie se lit dans `result.output`.
    out = CliRunner().invoke(app, ["cv", str(offre)]).output

    assert (
        "[Appel A] Tentative 1 : 2 violation(s) traçabilité, 1 autre(s) problème(s)."
        in out
    )
    assert (
        "[Appel B] Tentative 2 : 0 violation(s) traçabilité, 2 dépassement(s) comptage."
        in out
    )
    assert "429 reçu, nouvelle tentative dans 2.5s (1/5)" in out
    assert "[Remplissage] 3 rendu(s) Typst — 4 entrée(s) / 9 bullet(s) ;" in out


def test_cli_letter_still_prints_progress(monkeypatch, tmp_path):
    """`jobtool letter` affiche TOUJOURS la progression de ses deux passes."""
    from typer.testing import CliRunner

    from jobtool.cli.main import app

    def fake_generate(base, job_offer, **kw):
        kw["on_attempt"](1, 1, ["v"])
        kw["on_attempt"](2, 3, ["v1", "v2"])
        kw["on_retry"](2, 4.0, 5)
        raise RuntimeError("stop avant l'assemblage")

    # La commande ne câble plus la chaîne elle-même : elle passe par
    # `batch_service.process_letter`, qui RELAIE ses callbacks. C'est donc là que
    # `generate_cover_letter` est importé par son nom, et donc là qu'il faut le remplacer
    # (même idiome que `call_structured`, importé par son nom dans correction_loop).
    monkeypatch.setattr(
        "jobtool.services.batch_service.generate_cover_letter", fake_generate
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "clé-factice")
    offre = tmp_path / "offre.txt"
    offre.write_text("Une offre.", encoding="utf-8")
    # Base VALIDE (celle d'exemple du projet) : on teste l'affichage, pas le chargement.
    base = SRC.parents[1] / "data" / "base.example.yaml"

    out = (
        CliRunner()
        .invoke(app, ["letter", "--offre", str(offre), "--base", str(base)])
        .output
    )

    assert "[Passe 1] Tentative 1 : 1 violation(s)." in out
    assert "[Passe 2] Tentative 3 : 2 violation(s)." in out
    assert "429 reçu, nouvelle tentative dans 4.0s (2/5)" in out


# --- Points d'injection présents sur les entrées publiques ------------------


def test_public_entry_points_accept_callbacks():
    """Les signatures exposent bien les points d'injection, avec None (silence) par défaut."""
    import inspect

    from jobtool.documents.cover_letter.generate import generate_cover_letter
    from jobtool.documents.cv.generate import generate_tailored_cv_checked
    from jobtool.services.generation_service import run_single

    expected = {
        generate_tailored_cv_checked: ["on_attempt", "on_progress", "on_retry"],
        generate_cover_letter: ["on_attempt", "on_retry"],
        run_single: ["on_attempt", "on_progress", "on_retry"],
    }
    for func, names in expected.items():
        params = inspect.signature(func).parameters
        for name in names:
            assert name in params, f"{func.__name__} n'expose pas {name}"
            assert params[name].default is None, (
                f"{func.__name__}.{name} doit valoir None (silence) par défaut"
            )
