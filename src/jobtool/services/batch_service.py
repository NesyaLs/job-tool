"""Service d'orchestration BATCH : un lot d'offres -> un CV + une lettre par offre.

Couche SERVICE (printless) appelée par la CLI run_batch. Itère les offres, produit pour
chacune un `SingleResult` (CV) et un `LetterResult` (lettre), persiste le `<slug>.json` du CV
et les PDF. AUCUN print, AUCUN argparse, AUCUNE écriture de rapport (_summary.md) ici : la
présentation (tableaux console + résumé markdown) et le parsing CLI vivent dans run_batch.

La progression (par offre) est émise via un callback `report` optionnel (no-op par défaut),
pour rester printless tout en laissant la CLI afficher l'avancement à l'identique.

Aucune nouvelle logique métier : l'orchestration CV mono-offre est déléguée à
services.generation_service (run_single / render_and_check / compute_issues) et la lettre
réutilise le câblage documents.cover_letter (generate_cover_letter -> render_cover_letter).
Dépend de core.*, documents.* et services.generation_service ; ne remonte jamais vers une
couche d'interface (cli/api), et ne dépend pas d'aggregator.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.llm.client import RetryFn
from jobtool.core.rendering.pdf_checks import (
    AtsCheckError,
    check_pdf_ats,
    check_single_page,
    count_pdf_pages,
)
from jobtool.core.traceability import Violation
from jobtool.documents.cover_letter.generate import (
    LetterAttemptFn,
    generate_cover_letter,
)
from jobtool.documents.cover_letter.registry import (
    DEFAULT_LETTER_TEMPLATE,
    LetterTemplateError,
)
from jobtool.documents.cover_letter.render import render_cover_letter
from jobtool.documents.cover_letter.schema import (
    CoverLetterRender,
    CoverLetterRoutingOutput,
)
from jobtool.documents.cv.profiles import TemplateProfile
from jobtool.documents.cv.render import RenderError
from jobtool.documents.cv.schema import TailoredCV
from jobtool.services.generation_service import (
    SingleResult,
    compute_issues,
    render_and_check,
    run_single,
)

#: Callback de progression (message -> None). Par défaut no-op : le service reste printless.
ProgressFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    """Reporter par défaut : n'émet rien (service silencieux hors CLI)."""


# --- traitement d'une offre selon le mode -----------------------------------


def process_generate(
    base: ExperienceBase,
    profile: TemplateProfile,
    offer_text: str,
    out_pdf: Path,
    template: str,
) -> SingleResult:
    """Mode défaut : générer (API) -> rendre -> checker, sans jamais lever."""
    return run_single(
        base,
        offer_text,
        out_pdf,
        template=template,
        profile=profile,
        max_retries=2,
        strict=False,  # rend même un CV invalide, capture les erreurs
    )


def process_render_only(
    base: ExperienceBase,
    profile: TemplateProfile,
    cv: TailoredCV,
    out_pdf: Path,
    template: str,
) -> SingleResult:
    """Mode --render-only : pas d'API. Recalcule les violations (code) puis rend + checke."""
    result = SingleResult(cv=cv)
    result.violations, result.overruns = compute_issues(base, cv, profile)
    try:
        pdf_path, pages, page_violations, missing = render_and_check(
            base, cv, out_pdf, template
        )
    except (RenderError, AtsCheckError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.pdf_path = pdf_path
    result.pages = pages
    result.page_violations = page_violations
    result.missing = missing
    result.rendered = True
    return result


# --- lettre de motivation : génération + rendu + contrôles (ne lève jamais) --


@dataclass
class LetterResult:
    """Issue de la lettre d'une offre : mode décidé, PDF, contrôles, ou échec capturé.

    `error` non vide == échec (génération, non-convergence d'une passe, rendu, contrôle PDF) :
    on le NOTE et on continue le lot, jamais d'arrêt global. Débordement 2 pages = signalé
    via `page_violations` (jamais une troncature)."""

    mode: str | None = None
    pdf_path: str | None = None
    pages: int | None = None
    page_violations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rendered: bool = False
    error: str | None = None
    # Données prêtes-à-rendre retenues : permettent de RECOMPILER la lettre (retouche
    # manuelle) sans rappeler le LLM. Nul si la génération n'a pas abouti.
    render: CoverLetterRender | None = None
    # La décision de la passe 1, telle quelle. `mode` en est un extrait conservé pour les
    # appelants qui n'ont besoin que de lui (cli/batch.py) ; l'objet complet porte en plus
    # l'entreprise et le domaine, qu'une interface veut afficher.
    routing: CoverLetterRoutingOutput | None = None
    # Violations RESTANTES de chaque passe, structurées. `error` en donne un résumé d'une
    # ligne (c'est lui le signal d'échec pour les appelants) ; ces listes gardent le détail
    # pour qui veut l'afficher violation par violation, sans le réextraire d'une chaîne.
    routing_issues: list[Violation] = field(default_factory=list)
    draft_issues: list[Violation] = field(default_factory=list)


def process_letter(
    base: ExperienceBase,
    offer_text: str,
    out_pdf: Path,
    *,
    on_attempt: LetterAttemptFn | None = None,
    on_retry: RetryFn | None = None,
    template: str = DEFAULT_LETTER_TEMPLATE,
) -> LetterResult:
    """Pipeline lettre d'UNE offre : generate_cover_letter -> rendu PDF -> contrôles.

    SEUL enchaînement lettre du projet : `jobtool letter` et l'API passent tous deux par ici.
    Ne lève jamais : toute erreur d'étape est capturée dans `error` pour que le batch continue.
    Non-convergence d'une passe = échec lisible (quelle passe + violations).

    `on_attempt` / `on_retry` sont RELAYÉS tels quels à la chaîne LLM ; absents (défaut), le
    service est silencieux — il reste printless, et seule une interface les branche.

    `template` : nom d'un template de lettre (cover_letter/registry.py). Un nom inconnu est
    capturé dans `error` au rendu, comme toute autre erreur d'étape.
    """
    result = LetterResult()
    try:
        cl = generate_cover_letter(
            base, offer_text, on_attempt=on_attempt, on_retry=on_retry
        )
    except Exception as exc:  # réseau, 429 persistant, réponse inexploitable...
        result.error = f"génération : {type(exc).__name__}: {exc}"
        return result

    result.mode = cl.routing.mode
    result.routing = cl.routing
    result.routing_issues = list(cl.routing_issues)
    result.draft_issues = list(cl.draft_issues)
    if cl.routing_issues:
        result.error = "passe 1 (routage) non convergée : " + " ; ".join(
            str(v) for v in cl.routing_issues
        )
        return result
    if cl.draft_issues:
        result.error = "passe 2 (rédaction) non convergée : " + " ; ".join(
            str(v) for v in cl.draft_issues
        )
        return result

    result.render = cl.render

    try:
        pdf_path = render_cover_letter(cl.render, str(out_pdf), template)
    except (RenderError, LetterTemplateError) as exc:
        result.error = f"rendu : {exc}"
        return result
    result.pdf_path = pdf_path
    result.rendered = True

    # Contrôles PDF : nom + entreprise + date doivent ressortir du texte extrait.
    # Les cibles viennent du RENDU (`cl.render.nom`), pas de la base (`base.profile.name`) :
    # check_pdf_ats vérifie qu'une chaîne est ré-extractible du PDF, donc la référence est ce
    # qui a été écrit dedans. Les deux valeurs coïncident aujourd'hui (assemble.py pose
    # `nom=profile.name`) ; le jour où l'assemblage formate le nom, partir de la base
    # vérifierait une chaîne absente du PDF — un contrôle ATS qui passe en mentant.
    try:
        result.pages = count_pdf_pages(pdf_path)
        result.page_violations = check_single_page(pdf_path)
        result.missing = check_pdf_ats(
            pdf_path, [cl.render.nom, cl.routing.entreprise, cl.render.date]
        )
    except AtsCheckError as exc:
        result.error = f"contrôle PDF : {exc}"
        return result
    return result


# --- itération sur le lot (renvoie les résultats ; progression via `report`) -


def collect_generate(
    base: ExperienceBase,
    profile: TemplateProfile,
    *,
    offres_dir: Path,
    out_dir: Path,
    template: str,
    sleep: float,
    report: ProgressFn = _noop,
) -> tuple[list[tuple[str, SingleResult]], dict[str, LetterResult]]:
    """Itère les *.txt du dossier d'offres ; écrit <slug>.json pour chaque CV retenu."""
    offres_dir = Path(offres_dir)
    files = sorted(offres_dir.glob("*.txt"))
    if not files:
        report(f"Aucune offre .txt dans {offres_dir} — rien à faire.")
        return [], {}

    rows: list[tuple[str, SingleResult]] = []
    letters: dict[str, LetterResult] = {}
    for i, offer_file in enumerate(files, start=1):
        slug = offer_file.stem
        report(f"[{i}/{len(files)}] {slug} …")
        try:
            offer_text = offer_file.read_text(encoding="utf-8")
        except OSError as exc:
            rows.append((slug, SingleResult(error=f"lecture offre : {exc}")))
            continue

        # --- CV (comportement HISTORIQUE inchangé) ---------------------------
        out_pdf = out_dir / f"{slug}.pdf"
        result = process_generate(base, profile, offer_text, out_pdf, template)

        # Un CV produit est TOUJOURS persisté (même avec violations) pour pouvoir
        # le rejuger en --render-only sans rappeler l'API.
        if result.cv is not None:
            (out_dir / f"{slug}.json").write_text(
                result.cv.model_dump_json(indent=2), encoding="utf-8"
            )
        if result.error:
            report(f"    ! {result.error}")
        rows.append((slug, result))

        # Pause CV -> LETTRE d'une MÊME offre : on enchaîne d'autres appels LLM
        # rapprochés (passe 1 + passe 2). On espace avec une FRACTION de sleep
        # (moitié) ; le 429 backoff de core/llm reste le filet réseau interne.
        if sleep:
            time.sleep(sleep / 2)

        # --- LETTRE : <slug>.lettre.pdf à côté du CV -------------------------
        letter = process_letter(base, offer_text, out_dir / f"{slug}.lettre.pdf")
        if letter.error:
            report(f"    ! lettre : {letter.error}")
        letters[slug] = letter

        # Pause anti rate-limit Mistral entre offres (sauf après la dernière).
        if sleep and i < len(files):
            time.sleep(sleep)
    return rows, letters


def collect_render_only(
    base: ExperienceBase,
    profile: TemplateProfile,
    *,
    out_dir: Path,
    template: str,
    report: ProgressFn = _noop,
) -> tuple[list[tuple[str, SingleResult]], dict[str, LetterResult]]:
    """Relit les <slug>.json déjà produits dans out_dir, les rend + checke (sans API).

    PAS de lettre ici : le mode --render-only ne rappelle aucun LLM et on ne persiste pas
    le routing/draft de la lettre. Renvoie donc un dict de lettres VIDE."""
    files = sorted(out_dir.glob("*.json"))
    if not files:
        report(f"Aucun <slug>.json dans {out_dir} — rien à (re)rendre.")
        return [], {}

    rows: list[tuple[str, SingleResult]] = []
    for i, json_file in enumerate(files, start=1):
        slug = json_file.stem
        report(f"[{i}/{len(files)}] {slug} (render-only) …")
        try:
            cv = TailoredCV.model_validate_json(json_file.read_text(encoding="utf-8"))
        except Exception as exc:  # JSON cassé / schéma invalide
            rows.append((slug, SingleResult(error=f"json invalide : {exc}")))
            continue
        out_pdf = out_dir / f"{slug}.pdf"
        result = process_render_only(base, profile, cv, out_pdf, template)
        if result.error:
            report(f"    ! {result.error}")
        rows.append((slug, result))
    return rows, {}
