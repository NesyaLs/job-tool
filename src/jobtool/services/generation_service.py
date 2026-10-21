"""Service d'orchestration mono-offre : générer -> rendre -> checker.

Couche SERVICE partagée par les CLI run_pipeline (une offre, mode strict) et run_batch
(lot d'offres, mode tolérant), pour qu'elles appellent EXACTEMENT la même mécanique sans la
dupliquer. AUCUN print ni aucune sortie terminal ici : la présentation (affichage verbeux,
résumés) appartient à la couche CLI. Le service renvoie un `SingleResult` (et, en mode strict,
lève `PipelineAborted` porteur du résultat partiel pour que l'appelant puisse l'afficher).

Deux modes, pilotés par `strict` :
  - strict=True  : on ne rend PAS un CV qui viole traçabilité OU budget (PipelineAborted) ;
                   les erreurs d'étape (API, rendu, contrôle) remontent telles quelles.
  - strict=False : on rend MÊME un CV avec violations (jugé au visuel) ; toute erreur d'étape
                   est capturée dans SingleResult.error au lieu de planter (le lot continue).

Aucune nouvelle logique métier : réutilise generate_tailored_cv_checked (boucle de correction),
render_cv et les checks PDF. check_budget / traceability / profiles ne sont pas touchés.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jobtool.core.experience_base.models import PROJECT_TYPES, ExperienceBase
from jobtool.core.llm.client import RetryFn
from jobtool.core.rendering.pdf_checks import (
    AtsCheckError,
    check_pdf_ats,
    check_single_page,
    count_pdf_pages,
)
from jobtool.core.traceability import Violation, check_traceability
from jobtool.documents.cv.ats_targets import expected_strings
from jobtool.documents.cv.budget import check_budget
from jobtool.documents.cv.generate import (
    CvAttemptFn,
    CvProgressFn,
    generate_tailored_cv_checked,
)
from jobtool.documents.cv.profiles import TemplateProfile
from jobtool.documents.cv.render import RenderError, render_cv
from jobtool.documents.cv.schema import TailoredCV


@dataclass
class SingleResult:
    """Issue d'une offre : CV retenu, violations, PDF et résultats de contrôle."""

    cv: TailoredCV | None = None
    violations: list[Violation] = field(default_factory=list)
    overruns: list[str] = field(default_factory=list)
    pdf_path: str | None = None
    pages: int | None = None
    page_violations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rendered: bool = False
    error: str | None = None  # erreur d'étape capturée (mode non strict)

    @property
    def is_valid(self) -> bool:
        """Vrai si ni violation de traçabilité ni dépassement de budget."""
        return not self.violations and not self.overruns

    @property
    def num_violations(self) -> int:
        """Nombre total de manquements (traçabilité + budget)."""
        return len(self.violations) + len(self.overruns)


class PipelineAborted(RuntimeError):
    """Mode strict : le CV viole traçabilité/budget, on refuse de le rendre.

    Porte le `SingleResult` partiel (CV généré + violations) pour que la couche CLI
    puisse l'afficher avant d'annoncer l'annulation, sans rien réimprimer côté service.
    """

    def __init__(self, result: SingleResult):
        super().__init__("génération non conforme (traçabilité/budget) : rendu refusé")
        self.result = result


# --- comptages pour le résumé batch -----------------------------------------


def count_entries_projects_bullets(
    base: ExperienceBase, cv: TailoredCV
) -> tuple[int, int, int]:
    """(nb entrées, nb projets, nb bullets) d'un CV, pour le tableau de résumé."""
    type_by_id = {exp.id: exp.type.value for exp in base.experiences}
    entries = len(cv.experiences)
    projects = sum(
        1
        for ex in cv.experiences
        if type_by_id.get(ex.source_experience_id) in PROJECT_TYPES
    )
    bullets = sum(len(ex.bullets) for ex in cv.experiences)
    return entries, projects, bullets


def compute_issues(
    base: ExperienceBase, cv: TailoredCV, profile: TemplateProfile
) -> tuple[list[Violation], list[str]]:
    """(violations traçabilité, dépassements budget) d'un CV déjà produit.

    Utilisé en mode --render-only où l'on relit un <slug>.json sans rappeler le LLM,
    mais où l'on veut quand même rapporter les violations (checks 100 % code).
    """
    violations = list(check_traceability(base, cv))
    overruns = list(check_budget(base, cv, profile))
    return violations, overruns


# --- orchestration mono-offre -----------------------------------------------


def render_and_check(
    base: ExperienceBase, cv: TailoredCV, out_pdf: str | Path, template: str
) -> tuple[str, int, list[str], list[str]]:
    """Rend le PDF puis le contrôle. (chemin_pdf, nb_pages, viol_pages, fragments_absents).

    Briques pures (pas de clé API), utilisé par le mode --render-only de run_batch.
    `run_single` inline les mêmes étapes pour distinguer la capture d'erreur selon `strict`.
    """
    pdf_path = render_cv(base, cv, str(out_pdf), template=template)
    pages = count_pdf_pages(pdf_path)
    page_violations = check_single_page(pdf_path)
    missing = check_pdf_ats(pdf_path, expected_strings(base, cv))
    return pdf_path, pages, page_violations, missing


def run_single(
    base: ExperienceBase,
    job_offer: str,
    out_pdf: str | Path,
    *,
    template: str,
    profile: TemplateProfile,
    max_retries: int = 2,
    strict: bool,
    on_attempt: CvAttemptFn | None = None,
    on_progress: CvProgressFn | None = None,
    on_retry: RetryFn | None = None,
) -> SingleResult:
    """Générer -> (rendre -> checker) une offre. Voir l'en-tête du module pour les modes.

    strict=True  : lève PipelineAborted (porteur du résultat partiel) si invalide ; les
                   erreurs d'étape remontent (l'appelant CLI les traduit).
    strict=False : rend même un CV invalide ; capture les erreurs dans result.error.

    Les callbacks d'observabilité sont RELAYÉS tels quels au domaine ; absents (défaut), la
    génération est silencieuse — ce service reste printless, et l'API n'en branche aucun.
    """
    result = SingleResult()

    # --- 1. Génération + boucle de correction (traçabilité ET budget) ---------
    try:
        cv, violations, overruns = generate_tailored_cv_checked(
            base,
            job_offer,
            max_retries=max_retries,
            profile=profile,
            template=template,
            on_attempt=on_attempt,
            on_progress=on_progress,
            on_retry=on_retry,
        )
    except Exception as exc:  # réseau, API, clé invalide, réponse inexploitable...
        if strict:
            raise
        result.error = f"génération : {type(exc).__name__}: {exc}"
        return result

    result.cv = cv
    result.violations = violations
    result.overruns = overruns

    # --- 2. Garde-fou strict : pas de rendu si traçabilité OU budget échoue ----
    if strict and (violations or overruns):
        raise PipelineAborted(result)

    # --- 3. Rendu PDF ----------------------------------------------------------
    try:
        pdf_path = render_cv(base, cv, str(out_pdf), template=template)
    except RenderError:
        if strict:
            raise
        result.error = "rendu : échec de la compilation Typst"
        return result

    result.pdf_path = pdf_path
    result.rendered = True

    # --- 4. Contrôles du PDF : pagination + extractibilité ATS -----------------
    try:
        pages = count_pdf_pages(pdf_path)
        page_violations = check_single_page(pdf_path)
        missing = check_pdf_ats(pdf_path, expected_strings(base, cv))
    except AtsCheckError:
        if strict:
            raise
        result.error = "contrôle PDF : pdftotext/pdfinfo indisponible ou en échec"
        return result

    result.pages = pages
    result.page_violations = page_violations
    result.missing = missing

    return result
