"""Commande `jobtool batch` : un dossier d'offres .txt -> un CV ET une lettre par offre.

FORMAT D'ENTRÉE ATTENDU
  - Un dossier (défaut : data/offres_test/) contenant UNE offre par fichier .txt.
  - Le contenu du fichier est l'offre en texte brut (copier-coller de l'annonce).
  - Le nom du fichier (sans .txt) sert de SLUG : il nomme <slug>.json, <slug>.pdf (CV) et
    <slug>.lettre.pdf (lettre) dans le dossier de sortie. Ex : devops-acme.txt ->
    devops-acme.pdf + devops-acme.lettre.pdf
  - Les fichiers sont traités par ordre alphabétique de nom.

MODES
  - défaut : génère (appel Mistral, clé requise). Par offre : (1) CV -> <slug>.json ->
             <slug>.pdf -> checke (1 page, ATS, violations budget/traçabilité) ; puis
             (2) LETTRE -> <slug>.lettre.pdf -> checke (1 page, ATS). Une offre qui échoue
             (CV OU lettre) est MARQUÉE et N'INTERROMPT PAS le lot. Un CV avec violations
             est CONSERVÉ et marqué (jamais silencieusement faux).
  - --render-only : NE génère PAS (aucune clé requise). Relit les <slug>.json déjà présents
             dans --out et (re)rend + (re)checke le CV. PAS de lettre en render-only (le
             routing/draft de la lettre n'est pas persisté).

CE MODULE NE CONTIENT QUE DE LA PRÉSENTATION : tableaux console et `_summary.md`.
L'itération sur le lot vit dans `services.batch_service` (`collect_generate` /
`collect_render_only`), qui reste printless et émet sa progression par le callback `report`.
Cette séparation n'est pas cosmétique : le script `scripts/run_batch.py` d'où vient ce
fichier portait sa PROPRE copie de cette itération, laquelle avait déjà divergé du service
(elle ne retenait pas `LetterResult.render`, dont dépend la retouche de lettre).

THROTTLING anti-429 : --sleep (défaut 8 s) entre offres ; --sleep/2 entre le CV et la lettre
d'une même offre. Le retry 429 de core/llm reste le filet réseau.

Imports lourds (services -> SDK Mistral, rendu) DIFFÉRÉS dans les fonctions, pour que
`jobtool batch --help` réponde sans clé ni dépendance réseau.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from jobtool.documents.cv.profiles import PROFILES

if TYPE_CHECKING:  # annotations seules : rien n'est importé à l'exécution
    from jobtool.core.experience_base.models import ExperienceBase
    from jobtool.services.batch_service import LetterResult
    from jobtool.services.generation_service import SingleResult

# Chemins relatifs au répertoire de lancement (cwd), comme le reste de l'outil.
DEFAULT_OFFRES_DIR = "data/offres_test"
DEFAULT_BASE_PATH = "data/moi.yaml"
DEFAULT_OUT_DIR = "output/batch"

# Choix de --template dérivés de la source de vérité unique (cv/profiles.PROFILES), comme
# pour `jobtool cv` : tout preset ajouté à profiles.py est accepté sans rééditer ce fichier.
TemplateChoice = Enum(  # type: ignore[misc]
    "TemplateChoice",
    {name.replace("-", "_"): name for name in sorted(PROFILES)},
    type=str,
)


# --- résumé : tableau console + _summary.md ---------------------------------

_HEADERS = [
    "slug",
    "entrées",
    "projets",
    "bullets",
    "budget",
    "1 page ?",
    "ATS ?",
    "violations",
]


def _cells(base: ExperienceBase, slug: str, res: SingleResult) -> list[str]:
    """Construit la ligne de tableau (chaînes) pour une offre."""
    from jobtool.services.generation_service import count_entries_projects_bullets

    if res.cv is not None:
        entries, projects, bullets = count_entries_projects_bullets(base, res.cv)
        entries_s, projects_s, bullets_s = str(entries), str(projects), str(bullets)
        budget = "OK" if not res.overruns else f"{len(res.overruns)} dép."
        violations_s = str(res.num_violations)
    else:
        entries_s = projects_s = bullets_s = budget = violations_s = "-"

    if res.error and not res.rendered:
        one_page = ats = "ERR"
    elif res.rendered:
        one_page = "oui" if (res.pages == 1 and not res.page_violations) else "non"
        ats = "oui" if not res.missing else "non"
    else:
        one_page = ats = "-"

    return [slug, entries_s, projects_s, bullets_s, budget, one_page, ats, violations_s]


def print_console_table(
    base: ExperienceBase, rows: list[tuple[str, SingleResult]]
) -> None:
    """Affiche le tableau de résumé aligné dans la console."""
    table = [_cells(base, slug, res) for slug, res in rows]
    widths = [
        (
            max(len(_HEADERS[c]), *(len(r[c]) for r in table))
            if table
            else len(_HEADERS[c])
        )
        for c in range(len(_HEADERS))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[c]) for c, cell in enumerate(cells))

    print()
    print(fmt(_HEADERS))
    print("  ".join("-" * w for w in widths))
    for cells in table:
        print(fmt(cells))
    print()


def _detail_lines(res: SingleResult) -> list[str]:
    """Détail lisible des manquements d'une offre (vide si tout va bien)."""
    lines: list[str] = []
    if res.error:
        lines.append(f"- ERREUR : {res.error}")
    for violation in res.violations:
        lines.append(f"- traçabilité : {violation}")
    for overrun in res.overruns:
        lines.append(f"- budget : {overrun}")
    for page_violation in res.page_violations:
        lines.append(f"- pagination : {page_violation}")
    for fragment in res.missing:
        lines.append(f"- ATS manquant : {fragment!r}")
    return lines


def print_console_details(rows: list[tuple[str, SingleResult]]) -> None:
    """Liste, sous le tableau, les manquements détaillés par offre."""
    detailed = [(slug, _detail_lines(res)) for slug, res in rows]
    detailed = [(slug, lines) for slug, lines in detailed if lines]
    if not detailed:
        print("Aucune violation : tous les CV sont dans les clous.")
        return
    print("Violations détaillées :")
    for slug, lines in detailed:
        print(f"  {slug} :")
        for line in lines:
            print(f"    {line}")


# --- résumé LETTRE : colonnes + détails (section additionnelle, CV non touché) ---

_LETTER_HEADERS = ["slug", "mode lettre", "1 page ?", "ATS ?", "PDF lettre / statut"]


def _letter_cells(slug: str, lr: LetterResult | None) -> list[str]:
    """Ligne de tableau (chaînes) pour la lettre d'une offre (None = non générée)."""
    if lr is None:
        return [slug, "-", "-", "-", "(non générée)"]
    mode = lr.mode or "-"
    if lr.rendered:
        one_page = "oui" if (lr.pages == 1 and not lr.page_violations) else "non"
        ats = "oui" if not lr.missing else "non"
        status = Path(lr.pdf_path).name if lr.pdf_path else "(rendu ?)"
    elif lr.error:
        one_page = ats = "ERR"
        status = "ÉCHEC"
    else:
        one_page = ats = "-"
        status = "-"
    return [slug, mode, one_page, ats, status]


def _letter_detail_lines(lr: LetterResult | None) -> list[str]:
    """Détail lisible des manquements de la lettre d'une offre (vide si tout va bien)."""
    if lr is None:
        return []
    lines: list[str] = []
    if lr.error:
        lines.append(f"- ÉCHEC : {lr.error}")
    for page_violation in lr.page_violations:
        lines.append(f"- pagination : {page_violation}")
    for fragment in lr.missing:
        lines.append(f"- ATS manquant : {fragment!r}")
    return lines


def print_console_letters(
    rows: list[tuple[str, SingleResult]], letters: dict[str, LetterResult]
) -> None:
    """Affiche un petit tableau lettre dans la console (sauté si aucune lettre — render-only)."""
    if not letters:
        return
    table = [_letter_cells(slug, letters.get(slug)) for slug, _ in rows]
    widths = [
        (
            max(len(_LETTER_HEADERS[c]), *(len(r[c]) for r in table))
            if table
            else len(_LETTER_HEADERS[c])
        )
        for c in range(len(_LETTER_HEADERS))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[c]) for c, cell in enumerate(cells))

    print("Lettres de motivation :")
    print(fmt(_LETTER_HEADERS))
    print("  ".join("-" * w for w in widths))
    for cells in table:
        print(fmt(cells))
    print()


def write_summary_md(
    base: ExperienceBase,
    rows: list[tuple[str, SingleResult]],
    letters: dict[str, LetterResult],
    path: Path,
) -> None:
    """Écrit <out>/_summary.md : tableau CV + violations CV (INCHANGÉS), puis section LETTRE."""
    out: list[str] = ["# Résumé du batch", ""]
    out.append("## CV")
    out.append("")
    out.append("| " + " | ".join(_HEADERS) + " |")
    out.append("|" + "|".join("---" for _ in _HEADERS) + "|")
    for slug, res in rows:
        out.append("| " + " | ".join(_cells(base, slug, res)) + " |")
    out.append("")

    out.append("## Violations détaillées")
    out.append("")
    any_detail = False
    for slug, res in rows:
        lines = _detail_lines(res)
        if not lines:
            continue
        any_detail = True
        out.append(f"### {slug}")
        out.extend(lines)
        out.append("")
    if not any_detail:
        out.append("Aucune violation : tous les CV sont dans les clous.")
        out.append("")

    # --- Section LETTRE (additionnelle ; n'écrase pas la partie CV) ----------
    out.append("## Lettres de motivation")
    out.append("")
    if not letters:
        out.append("(aucune lettre générée — mode --render-only)")
        out.append("")
    else:
        out.append("| " + " | ".join(_LETTER_HEADERS) + " |")
        out.append("|" + "|".join("---" for _ in _LETTER_HEADERS) + "|")
        for slug, _ in rows:
            out.append("| " + " | ".join(_letter_cells(slug, letters.get(slug))) + " |")
        out.append("")

        out.append("### Échecs / alertes lettre")
        out.append("")
        any_letter_detail = False
        for slug, _ in rows:
            lines = _letter_detail_lines(letters.get(slug))
            if not lines:
                continue
            any_letter_detail = True
            out.append(f"#### {slug}")
            out.extend(lines)
            out.append("")
        if not any_letter_detail:
            out.append("Aucun échec : toutes les lettres sont rendues, 1 page, ATS OK.")
            out.append("")

    path.write_text("\n".join(out), encoding="utf-8")


# --- entrée CLI -------------------------------------------------------------


def batch(
    offres: str = typer.Option(
        DEFAULT_OFFRES_DIR, help="dossier d'offres .txt (défaut : data/offres_test/)"
    ),
    base: str = typer.Option(
        DEFAULT_BASE_PATH, help="chemin du YAML de base (défaut : data/moi.yaml)"
    ),
    template: TemplateChoice = typer.Option(
        TemplateChoice("compact"), help="template de mise en page (défaut : compact)"
    ),
    out: str = typer.Option(
        DEFAULT_OUT_DIR, help="dossier de sortie (défaut : output/batch/)"
    ),
    render_only: bool = typer.Option(
        False,
        "--render-only",
        help="ne génère PAS : relit les <slug>.json de --out et (re)rend + (re)checke "
        "(sans clé).",
    ),
    sleep: float = typer.Option(
        8.0,
        help="secondes d'attente entre deux offres (défaut : 8, anti rate-limit Mistral). "
        "Une pause de --sleep/2 est aussi appliquée entre le CV et la lettre d'une même "
        "offre.",
    ),
) -> None:
    """Génère un CV et une lettre pour chaque offre .txt d'un dossier."""
    # Imports lourds (services -> SDK Mistral, loader, rendu) déférés : `--help` ne dépend
    # de rien. Toute l'itération vit dans le service ; cette commande ne fait que présenter.
    from dotenv import load_dotenv

    from jobtool.core.experience_base.loader import BaseLoadError, load_base
    from jobtool.documents.cv.profiles import get_profile
    from jobtool.services.batch_service import collect_generate, collect_render_only

    try:
        experience_base = load_base(Path(base))
    except BaseLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    # Le BON budget vient du profil du template choisi.
    profile = get_profile(template.value)

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    load_dotenv()

    # `report` rebranche ICI l'affichage de progression : le service reste printless, et
    # c'est la couche d'interface qui décide d'écrire sur le terminal.
    if render_only:
        rows, letters = collect_render_only(
            experience_base,
            profile,
            out_dir=out_dir,
            template=template.value,
            report=typer.echo,
        )
    else:
        if not os.environ.get("MISTRAL_API_KEY"):
            raise SystemExit(
                "Erreur : MISTRAL_API_KEY absente (mode génération).\n"
                "Copie .env.example en .env et renseigne ta clé Mistral, "
                "ou utilise --render-only pour relire des JSON existants sans clé."
            )
        rows, letters = collect_generate(
            experience_base,
            profile,
            offres_dir=Path(offres),
            out_dir=out_dir,
            template=template.value,
            sleep=sleep,
            report=typer.echo,
        )

    if not rows:
        return

    print_console_table(experience_base, rows)
    print_console_details(rows)
    print_console_letters(rows, letters)

    summary_path = out_dir / "_summary.md"
    write_summary_md(experience_base, rows, letters, summary_path)
    print(f"\nRésumé écrit : {summary_path}")
