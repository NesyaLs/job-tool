"""Commande `jobtool letter` : lettre de motivation de bout en bout -> PDF (mode strict).

PRÉSENTATION UNIQUEMENT. L'enchaînement (passe 1 -> passe 2 -> rendu -> contrôles PDF) vit
dans `services.batch_service.process_letter`, seul endroit du projet où il est écrit : cette
commande et l'API passent par le même code. Elle avait été recopiée ici, et les deux copies
avaient déjà divergé (cible ATS prise dans la base au lieu du rendu, contrôle de pagination
réécrit à la main à côté de `check_single_page`) — c'est un défaut d'audit qui avait été
déjà corrigé pour le batch.

Tout l'affichage vit ICI, jamais dans le service : la progression arrive par les callbacks
`on_attempt` / `on_retry` que seule la CLI branche. Les imports lourds (chaîne -> SDK Mistral)
sont DIFFÉRÉS dans la fonction pour que `jobtool letter --help` réponde sans clé ni mistralai.

Logging opt-in inchangé : JOBTOOL_LLM_LOG=1 -> appels LLM journalisés dans logs/llm/ par le
mécanisme existant de core/llm/client. L'appel réseau n'a lieu QUE dans la commande.
"""

import os
from enum import Enum
from pathlib import Path

import typer

from jobtool.cli._display import print_check_blocks
from jobtool.services.template_service import (
    DEFAULT_LETTER_TEMPLATE,
    offered_letter_templates,
)

# Choix de --template dérivés du registre (cover_letter/registry.py) : un template de lettre
# ajouté au registre est accepté ici sans rééditer la commande. Import léger (aucun SDK).
# (type: ignore : mypy ne sait pas introspecter une Enum fonctionnelle dynamique ; runtime OK.)
LetterTemplateChoice = Enum(  # type: ignore[misc]
    "LetterTemplateChoice",
    {spec.name: spec.name for spec in offered_letter_templates()},
    type=str,
)

# Chemins relatifs au répertoire de lancement (cwd), comme le reste de l'outil.
DEFAULT_BASE = "data/moi.yaml"
DEFAULT_OUT = "output/cover_letter/lettre.pdf"


def _print_violations(label: str, issues: list) -> None:
    """Affiche les violations restantes d'une passe non convergée (présentation CLI)."""
    print(
        f"\nÉCHEC : {label} n'a pas convergé — {len(issues)} violation(s) restante(s) :"
    )
    for v in issues:
        print(f"  - {v}")


def _print_attempt(passe: int, attempt: int, issues: list) -> None:
    """Progression d'une passe — texte identique à l'affichage historique."""
    print(f"[Passe {passe}] Tentative {attempt} : {len(issues)} violation(s).")


def _print_retry(retries: int, delay: float, maximum: int) -> None:
    """Attente après un 429 — texte identique à l'affichage historique."""
    print(f"429 reçu, nouvelle tentative dans {delay:.1f}s ({retries}/{maximum})")


def letter(
    offre: str = typer.Option(
        ..., help="fichier texte de l'offre d'emploi (lu depuis le disque)"
    ),
    base: str = typer.Option(
        DEFAULT_BASE, help="fichier YAML de base (défaut : data/moi.yaml)"
    ),
    out: str = typer.Option(
        DEFAULT_OUT,
        help="chemin du PDF de sortie (défaut : output/cover_letter/lettre.pdf)",
    ),
    template: LetterTemplateChoice = typer.Option(
        LetterTemplateChoice(DEFAULT_LETTER_TEMPLATE),
        help=f"mise en page de la lettre (défaut : {DEFAULT_LETTER_TEMPLATE})",
    ),
) -> None:
    """Lettre de motivation de bout en bout (passe 1 + passe 2 -> PDF) — VRAI appel LLM."""
    # Imports LOURDS (chaîne -> SDK Mistral) déférés : `--help` n'a besoin ni de clé ni du SDK.
    from dotenv import load_dotenv

    from jobtool.core.experience_base.loader import BaseLoadError, load_base
    from jobtool.services.batch_service import process_letter

    # .env -> MISTRAL_API_KEY (comme les autres commandes réelles). Garde-fou clé AVANT l'appel.
    load_dotenv()
    if not os.environ.get("MISTRAL_API_KEY"):
        raise SystemExit(
            "Erreur : MISTRAL_API_KEY absente.\n"
            "Copie .env.example en .env et renseigne ta clé Mistral."
        )

    # Base : BaseLoadError (message clair) traduite ICI en sortie CLI propre.
    try:
        base_obj = load_base(Path(base))
    except BaseLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    # Offre : lue depuis le disque (jamais passée en argument brut).
    offre_path = Path(offre)
    try:
        job_offer = offre_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"Erreur : offre introuvable : {offre_path}")
    if not job_offer.strip():
        raise SystemExit(f"Erreur : offre vide : {offre_path}")

    print(f"Base     : {base}")
    print(f"Offre    : {offre_path}")
    print(f"Template : {template.value}\n")

    # Le dossier de sortie reste la responsabilité de l'appelant : le service écrit le PDF
    # là où on le lui dit, il ne fabrique pas d'arborescence.
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # --- L'enchaînement complet, en un appel ---------------------------------
    # Le service est silencieux ; c'est la CLI qui branche l'affichage des tentatives.
    result = process_letter(
        base_obj,
        job_offer,
        out_path,
        on_attempt=_print_attempt,
        on_retry=_print_retry,
        template=template.value,
    )

    if result.routing is not None:
        print(f"\nMode décidé (passe 1) : {result.routing.mode}")
        print(f"Entreprise            : {result.routing.entreprise}")
        print(f"Domaine               : {result.routing.domaine}")

    # --- Convergence des deux passes (sinon on ne rend pas un PDF faussé) ----
    # Les listes structurées permettent d'afficher une violation par ligne ; `error` n'en est
    # qu'un résumé, on ne le réanalyse pas.
    if result.routing_issues:
        _print_violations("la passe 1 (routage)", result.routing_issues)
        raise typer.Exit(code=1)
    if result.draft_issues:
        _print_violations("la passe 2 (rédaction)", result.draft_issues)
        raise typer.Exit(code=1)

    # Tout autre échec capturé par le service (génération, rendu Typst, contrôle PDF).
    if result.error or not result.rendered:
        raise SystemExit(f"Erreur : {result.error or 'lettre non produite'}")

    print(f"\nPDF généré : {result.pdf_path}")

    # --- Contrôles PDF : 1 page + extractibilité (ATS) -----------------------
    print_check_blocks(
        result.pages or 0,
        result.page_violations,
        result.missing,
        ats_ok="Texte extractible (ATS) : OK (en-tête nom/entreprise/date retrouvés).",
    )
    if result.page_violations:
        # Débordement = problème de RÉGLAGE (plafonds passe 2), pas une coupe au rendu.
        print(
            "  À corriger en RÉDUISANT les plafonds de longueur de la passe 2 "
            "(draft_checks.py), pas en tronquant le rendu."
        )

    if result.page_violations or result.missing:
        raise typer.Exit(code=1)
    print("\nOK : lettre générée, 1 page, texte extractible.")
