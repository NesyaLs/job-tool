"""Preuve SANS clé API du BATCH ÉTENDU (CV + LETTRE par offre).

Stub du client (aiguillé par le SCHÉMA demandé : CV appel A/B, lettre passe 1/2) + make_client.
Le reste est RÉEL : on lance la VRAIE commande `jobtool batch` sur 2 offres factices, rendu
Typst réel. On compare le TEXTE EXTRAIT (pdftotext), jamais les octets (timestamp Typst).

Le test passe par la CLI installée, donc par `services.batch_service` — le code que l'API
utilise aussi. Il exerçait auparavant `scripts/run_batch.py`, qui portait sa propre copie de
cette itération : la preuve ne portait pas sur le code de production.

a_fail (traité avant b_ok, ordre alpha) : lettre volontairement en échec (passe 2 ne converge
jamais) -> notée ÉCHEC SANS arrêter le lot. b_ok : CV + lettre OK.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from jobtool.cli.main import app
from jobtool.core.rendering.pdf_checks import count_pdf_pages, extract_pdf_text
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)
from jobtool.documents.cv.schema import (
    TailoredBullet,
    TailoredExperience,
    TailoredExperiencesOutput,
    TailoredSkillsOutput,
)

_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]+\]")

PROFIL_DEMO = Path(__file__).resolve().parent / "fixtures" / "profil_demo.yaml"


# --- Sorties LLM fabriquées, cohérentes avec tests/fixtures/profil_demo.yaml --------------------
CV_EXPERIENCES = TailoredExperiencesOutput(
    experiences=[
        TailoredExperience(
            source_experience_id="stage-atlas-ia-2026",
            bullets=[
                TailoredBullet(
                    source_id="stage-atlas-ia-2026.1",
                    text="Travaux d'IA appliquée, orientés performance.",
                ),
                TailoredBullet(
                    source_id="stage-atlas-ia-2026.2",
                    text="Mise en place d'expériences reproductibles.",
                ),
            ],
        ),
        TailoredExperience(
            source_experience_id="stage-nordwind-2025",
            bullets=[
                TailoredBullet(
                    source_id="stage-nordwind-2025.1",
                    text="Conception de systèmes logiciels passant à l'échelle.",
                ),
                TailoredBullet(
                    source_id="stage-nordwind-2025.2",
                    text="Optimisation de composants critiques.",
                ),
            ],
        ),
        TailoredExperience(
            source_experience_id="stage-lumen-lab-2023",
            bullets=[
                TailoredBullet(
                    source_id="stage-lumen-lab-2023.1",
                    text="Contribution à un projet de recherche appliquée.",
                ),
            ],
        ),
        TailoredExperience(
            source_experience_id="projet-noyaux-hpc",
            bullets=[
                TailoredBullet(
                    source_id="projet-noyaux-hpc.1",
                    text="Noyaux de calcul séquentiels de référence.",
                ),
                TailoredBullet(
                    source_id="projet-noyaux-hpc.2",
                    text="Parallélisation OpenMP et MPI.",
                ),
            ],
        ),
        TailoredExperience(
            source_experience_id="projet-filtres-gpu",
            bullets=[
                TailoredBullet(
                    source_id="projet-filtres-gpu.1",
                    text="Filtres d'images parallélisés sur GPU.",
                ),
            ],
        ),
    ]
)
CV_SKILLS = TailoredSkillsOutput(
    hard_skills=["C++", "Python", "Go"],
    soft_skills=["Rigueur", "Autonomie"],
    interests=["Trail en montagne"],
    focus="",
)
ROUTING_CIBLE = CoverLetterRoutingOutput(
    entreprise="HPC Systems",
    domaine="calcul haute performance",
    mode="cible",
    projets_ids=["projet-noyaux-hpc.1", "projet-noyaux-hpc.2", "projet-noyaux-hpc.3"],
    interets_ids=["Contributions à des logiciels open source"],
)
_INSERT_PROJET = (
    "J'ai écrit une version séquentielle de référence de deux noyaux de calcul, puis "
    "je les ai parallélisés avec OpenMP et MPI avant de les porter sur GPU CUDA."
)
_INSERT_INTERET = "Je contribue à des projets open source, ce qui me garde proche des questions de performance."
DRAFT_OK = CoverLetterDraftOutput(
    paragraphe_vous="Le poste en calcul haute performance chez HPC Systems correspond à ce que je cherche.",
    insert_interet=_INSERT_INTERET,
    paragraphe_moi="Diplômé ingénieur en informatique, je suis à l'aise avec la performance et le parallélisme.",
    insert_projet=_INSERT_PROJET,
    paragraphe_nous="Je serais heureux d'échanger lors d'un entretien.",
    formule="Cordialement,",
)
# Draft qui NE CONVERGE JAMAIS : placeholder résiduel répété à chaque tentative.
DRAFT_BAD = DRAFT_OK.model_copy(
    update={
        "paragraphe_vous": "Le travail de [entreprise] en HPC m'intéresse depuis un moment."
    }
)


def make_fake_call_structured():
    """Stub aiguillé par le SCHÉMA demandé + le contenu de l'offre (POSTE_FAIL -> draft bancal)."""

    def fake_call_structured(client, messages, response_format, **kwargs):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        is_fail = "POSTE_FAIL" in joined
        if response_format is TailoredExperiencesOutput:
            return CV_EXPERIENCES
        if response_format is TailoredSkillsOutput:
            return CV_SKILLS
        if response_format is CoverLetterRoutingOutput:
            return ROUTING_CIBLE
        if response_format is CoverLetterDraftOutput:
            return DRAFT_BAD if is_fail else DRAFT_OK
        raise AssertionError(f"schéma inattendu dans le stub : {response_format}")

    return fake_call_structured


@pytest.fixture
def batch_run(typst_out_dir):
    out_dir = typst_out_dir
    offres_dir = out_dir / "_offres"
    offres_dir.mkdir(parents=True, exist_ok=True)
    # a_fail traité AVANT b_ok (ordre alpha) : prouve que le lot continue après un échec.
    (offres_dir / "a_fail.txt").write_text(
        "POSTE_FAIL ingénieur calcul haute performance.\n", encoding="utf-8"
    )
    (offres_dir / "b_ok.txt").write_text(
        "POSTE_OK ingénieur calcul haute performance.\n", encoding="utf-8"
    )

    args = [
        "batch",
        "--offres",
        str(offres_dir),
        "--base",
        str(PROFIL_DEMO),
        "--out",
        str(out_dir),
        "--template",
        "compact",
        "--sleep",
        "0",
    ]
    patches = [
        patch.dict("os.environ", {"MISTRAL_API_KEY": "stub-key-demo"}),
        patch(
            "jobtool.core.llm.correction_loop.call_structured",
            side_effect=make_fake_call_structured(),
        ),
        patch(
            "jobtool.documents.cv.generate.make_client", side_effect=lambda: object()
        ),
        patch(
            "jobtool.documents.cover_letter.passe1.make_client",
            side_effect=lambda: object(),
        ),
        patch(
            "jobtool.documents.cover_letter.passe2.make_client",
            side_effect=lambda: object(),
        ),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output + str(result.exception)
    return out_dir


def test_cv_generated_per_offer(batch_run):
    for tag in ("a_fail", "b_ok"):
        cv_pdf = batch_run / f"{tag}.pdf"
        assert cv_pdf.exists(), f"CV {tag} : PDF généré"
        assert count_pdf_pages(str(cv_pdf)) == 1, f"CV {tag} : 1 page"
        assert "Camille Martin" in extract_pdf_text(str(cv_pdf)), (
            f"CV {tag} : nom extractible (ATS)"
        )


def test_letter_ok_rendered_with_inserts(batch_run):
    lettre_b = batch_run / "b_ok.lettre.pdf"
    assert lettre_b.exists(), "lettre b_ok : PDF généré"
    text_b = extract_pdf_text(str(lettre_b))
    assert count_pdf_pages(str(lettre_b)) == 1, "lettre b_ok : 1 page"
    assert "HPC Systems" in text_b, "lettre b_ok : entreprise présente (en-tête)"
    assert "CUDA" in text_b, "lettre b_ok : insert PROJET présent (CUDA)"
    assert "open source" in text_b, "lettre b_ok : insert INTÉRÊT présent (open source)"
    assert not _PLACEHOLDER_RE.search(text_b), "lettre b_ok : aucun placeholder [..]"


def test_letter_failure_isolated(batch_run):
    assert not (batch_run / "a_fail.lettre.pdf").exists(), (
        "lettre a_fail : PAS de PDF (passe 2 non convergée)"
    )


def test_summary_extended(batch_run):
    summary = (batch_run / "_summary.md").read_text(encoding="utf-8")
    assert "## Lettres de motivation" in summary, "résumé : section Lettres présente"
    assert "## CV" in summary, "résumé : section CV préservée"
    assert "b_ok.lettre.pdf" in summary, "résumé : chemin du PDF lettre de b_ok présent"
    assert "cible" in summary, "résumé : mode lettre (cible) reporté"
    assert "ÉCHEC" in summary, "résumé : statut d'ÉCHEC reporté pour a_fail"
    assert "passe 2" in summary, "résumé : échec attribué à la bonne passe (passe 2)"
    assert summary.count("a_fail") >= 1 and summary.count("b_ok") >= 1, (
        "résumé : les DEUX offres listées (lot non interrompu)"
    )
