"""Commande `jobtool cv` : chaîne end-to-end de génération d'un CV (mode strict).

Reproduit exactement l'ancien scripts/run_pipeline.py : mêmes arguments (offre positionnelle,
--template, --name), mêmes défauts, chemins relatifs au CWD (convention actée pour
documents/cv). L'orchestration (générer -> rendre -> checker) est déléguée au service printless
jobtool.services.generation_service.run_single ; l'affichage verbeux est reconstruit ICI (couche
présentation) depuis le SingleResult — succès comme abort partiel (CV affiché avant l'annulation,
via PipelineAborted.result). Les imports lourds (service -> SDK Mistral, loader, rendu) sont
DIFFÉRÉS dans la fonction pour que `jobtool cv --help` réponde sans clé ni mistralai.
"""

import os
from enum import Enum
from pathlib import Path

import typer

from jobtool.cli._display import print_check_blocks
from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.traceability import Violation
from jobtool.documents.cv.profiles import PROFILES
from jobtool.documents.cv.schema import TailoredCV

# Choix de --template dérivés de la source de vérité unique (cv/profiles.PROFILES) : tout
# preset ajouté à profiles.py est automatiquement accepté, sans rééditer cette commande.
# Enum à VALEURS = noms de preset (avec tirets) ; noms de MEMBRES assainis (identifiants).
# (type: ignore : mypy ne sait pas introspecter une Enum fonctionnelle dynamique ; runtime OK.)
TemplateChoice = Enum(  # type: ignore[misc]
    "TemplateChoice",
    {name.replace("-", "_"): name for name in sorted(PROFILES)},
    type=str,
)

# Chemins relatifs au répertoire de lancement (cwd), comme le reste de l'outil.
BASE_PATH = Path("data/moi.yaml")
DEFAULT_OFFER_PATH = "data/offer.example.txt"
OUTPUT_DIR = Path("output")
DEFAULT_OUT_NAME = "cv.pdf"


# --- affichage verbeux (présentation CLI ; le service ne produit aucune sortie) ---


def print_hard_info(base: ExperienceBase) -> None:
    """Zone EN DUR : identité + formation, recopiées telles quelles (sans LLM)."""
    profile = base.profile
    print("=== INFO EN DUR (depuis la base, jamais le LLM) ===")
    print(f"Identité  : {profile.name} <{profile.email}>")
    if profile.phone or profile.location:
        print(f"            {profile.phone or ''} {profile.location or ''}".rstrip())
    print("Formation :")
    if not base.education:
        print("            (aucune)")
    for edu in base.education:
        print(f"            - {edu.degree} — {edu.school} ({edu.start} → {edu.end})")
    print()


def print_generated(cv: TailoredCV) -> None:
    """Zone GÉNÉRÉE : expériences tracées + compétences SÉLECTIONNÉES dans les listes maîtres."""
    print("=== INFO GÉNÉRÉE (depuis Mistral) ===")
    print("Expériences (tracées) :")
    for exp in cv.experiences:
        print(f"  [{exp.source_experience_id}]")
        for bullet in exp.bullets:
            print(f"    - ({bullet.source_id}) {bullet.text}")
    # Compétences/intérêts : SÉLECTIONNÉS à l'identique dans les listes maîtres (tracés).
    print(f"Hard skills (sélectionnés) : {', '.join(cv.hard_skills) or '(aucun)'}")
    print(f"Soft skills (sélectionnés) : {', '.join(cv.soft_skills) or '(aucun)'}")
    print(f"Centres d'intérêt (sélectionnés) : {', '.join(cv.interests) or '(aucun)'}")
    print()


def _print_issue_blocks(violations: list[Violation], overruns: list[str]) -> None:
    """États traçabilité + budget, exactement comme l'historique run_pipeline."""
    if violations:
        print(f"Traçabilité : {len(violations)} violation(s) résiduelle(s) :")
        for violation in violations:
            print(f"  - {violation}")
    else:
        print("Traçabilité : 0 violation — le CV ne référence que des éléments réels.")

    if overruns:
        print(f"Budget : {len(overruns)} dépassement(s) résiduel(s) :")
        for overrun in overruns:
            print(f"  - {overrun}")
    else:
        print("Budget : OK — toutes les limites sont respectées.")
    print()


def _print_attempt(label: str, attempt: int, violations: list, overruns: list) -> None:
    """Progression d'une tentative LLM — texte identique à l'affichage historique."""
    suffix = "autre(s) problème(s)" if label == "A" else "dépassement(s) comptage"
    print(
        f"[Appel {label}] Tentative {attempt} : {len(violations)} violation(s) traçabilité, "
        f"{len(overruns)} {suffix}."
    )


def _print_fill(report, error) -> None:
    """Issue du remplissage mesuré — texte identique à l'affichage historique."""
    if error is not None:
        print(
            f"[Remplissage] rendu impossible ({error}) — réservoir conservé sans remplissage."
        )
        return
    if report.floor_overflow:
        verdict = "PLANCHER déjà en débordement (> 1 page)"
    elif report.stopped_on_overflow:
        verdict = "page pleine, arrêt propre"
    else:
        verdict = "tout le réservoir tient sur 1 page"
    print(
        f"[Remplissage] {report.renders} rendu(s) Typst — "
        f"{report.final_entries} entrée(s) / {report.final_bullets} bullet(s) ; {verdict}."
    )


def _print_retry(retries: int, delay: float, maximum: int) -> None:
    """Attente après un 429 — texte identique à l'affichage historique."""
    print(f"429 reçu, nouvelle tentative dans {delay:.1f}s ({retries}/{maximum})")


def cv(
    offer: str = typer.Argument(
        DEFAULT_OFFER_PATH, help="chemin de l'offre (défaut : data/offer.example.txt)"
    ),
    template: TemplateChoice = typer.Option(
        TemplateChoice("classic"),
        help="template de mise en page (défaut : classic)",
    ),
    name: str = typer.Option(
        DEFAULT_OUT_NAME,
        help="nom du PDF de sortie dans output/ (défaut : cv.pdf). "
        "Permet de générer plusieurs CV sans écraser les précédents.",
    ),
) -> None:
    """Chaîne end-to-end de génération de CV (strict : ne rend pas un CV invalide)."""
    # Imports lourds (service -> SDK Mistral, loader, rendu) déférés : `--help` ne dépend de rien.
    from dotenv import load_dotenv

    from jobtool.core.experience_base.loader import BaseLoadError, load_base
    from jobtool.core.rendering.pdf_checks import AtsCheckError
    from jobtool.documents.cv.profiles import get_profile
    from jobtool.documents.cv.render import RenderError
    from jobtool.services.generation_service import PipelineAborted, run_single

    # Le PDF est toujours écrit dans output/ ; on ajoute .pdf si absent.
    out_name = name if name.lower().endswith(".pdf") else name + ".pdf"
    out_pdf = OUTPUT_DIR / Path(out_name).name  # .name : pas d'évasion hors de output/

    load_dotenv()

    if not os.environ.get("MISTRAL_API_KEY"):
        raise SystemExit(
            "Erreur : MISTRAL_API_KEY absente.\n"
            "Copie .env.example en .env et renseigne ta clé Mistral."
        )

    try:
        base = load_base(BASE_PATH)
    except BaseLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    offer_path = Path(offer)
    try:
        job_offer = offer_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"Erreur : offre introuvable : {offer_path}")

    print(f"Offre : {offer_path}\n")

    # Profil de contraintes du template choisi (le BON budget est vérifié dedans).
    profile = get_profile(template.value)

    # Affichage EN DUR (identité + formation), avant la génération — comme l'historique.
    print_hard_info(base)

    # Orchestration partagée, mode strict (refuse de rendre un CV invalide). Le service ne
    # produit aucune sortie : on reconstruit ici l'affichage verbeux depuis le résultat.
    # Les erreurs d'étape remontent typées : on les traduit en messages, à l'identique.
    try:
        result = run_single(
            base,
            job_offer,
            out_pdf,
            template=template.value,
            profile=profile,
            max_retries=2,
            strict=True,
            # Le domaine est silencieux par défaut : la CLI rebranche ICI son affichage
            # de progression (à l'identique), pendant que l'API n'en branche aucun.
            on_attempt=_print_attempt,
            on_progress=_print_fill,
            on_retry=_print_retry,
        )
    except PipelineAborted as exc:
        # Montre le CV généré + violations (portés par l'exception) avant l'annulation.
        # run_single garantit result.cv non-None quand il lève PipelineAborted (CV généré).
        assert exc.result.cv is not None
        print()
        print_generated(exc.result.cv)
        _print_issue_blocks(exc.result.violations, exc.result.overruns)
        raise SystemExit(
            "Rendu ANNULÉ : la génération n'a pas satisfait traçabilité et budget."
        )
    except AtsCheckError as exc:
        raise SystemExit(f"Erreur de contrôle : {exc}")
    except RenderError as exc:
        raise SystemExit(f"Erreur de rendu : {exc}")
    except Exception as exc:  # réseau, API, clé invalide, réponse inexploitable...
        raise SystemExit(f"Erreur lors de l'appel à Mistral : {exc}")

    # Succès : affichage verbeux reconstruit depuis le résultat, dans l'ordre historique.
    # En cas de succès, run_single a rendu + contrôlé : cv/pages/… sont renseignés.
    assert result.cv is not None and result.pages is not None
    print()
    print_generated(result.cv)
    _print_issue_blocks(result.violations, result.overruns)
    print(f"PDF généré : {result.pdf_path}\n")
    print_check_blocks(
        result.pages,
        result.page_violations,
        result.missing,
        ats_ok="PDF ATS OK — nom et bullets sont extractibles du PDF.",
    )
