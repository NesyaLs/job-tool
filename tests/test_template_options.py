"""Preuves (sans clé API) des 2 options d'affichage : soft skills + phrase d'accroche.

VRAI rendu Typst. Aucun appel réseau. Le paquet jobtool est installé en editable (pas de
sys.path.insert) et mistralai est une vraie dépendance (pas de stub).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)


def _load_base():
    from jobtool.core.experience_base.loader import load_base

    return load_base(ROOT / "data" / "base.example.yaml")


def _demo_cv(**update):
    from jobtool.documents.cv.schema import TailoredCV

    cv = TailoredCV.model_validate_json(
        (ROOT / "data" / "tailored.example.json").read_text()
    )
    return cv.model_copy(update=update) if update else cv


def _with_profile(base, **prof_update):
    """base avec Profile mis à jour (accroche/accroche_template)."""
    return base.model_copy(
        update={"profile": base.profile.model_copy(update=prof_update)}
    )


def _sections(pdf_path: str) -> str:
    from jobtool.core.rendering.pdf_checks import extract_pdf_text

    return extract_pdf_text(pdf_path)


# ---------------------------------------------------------------------------
# 1. Les 4 presets rendent ; baseline = pas de soft, pas d'accroche
# ---------------------------------------------------------------------------


class TestFourPresets(unittest.TestCase):
    def test_presets_exist(self):
        from jobtool.documents.cv.profiles import get_profile

        combos = {
            "compact": (False, False),
            "compact-soft": (True, False),
            "compact-ctx": (False, True),
            "compact-soft-ctx": (True, True),
        }
        for name, (soft, ctx) in combos.items():
            p = get_profile(name)
            self.assertEqual((p.show_soft_skills, p.show_accroche), (soft, ctx))
        print(
            "\n  ✓ 4 presets : compact / compact-soft / compact-ctx / compact-soft-ctx"
        )

    def test_four_presets_render_one_page_and_structure(self):
        from jobtool.core.rendering.pdf_checks import count_pdf_pages
        from jobtool.documents.cv.render import render_cv

        # base avec accroche fixe (nécessaire pour les presets -ctx).
        base = _with_profile(
            _load_base(),
            accroche="Étudiant en développement backend, à la recherche d'un stage de fin d'études.",
        )
        cv = _demo_cv()  # contient des soft skills

        expectations = {
            "compact": dict(soft=False, accroche=False),
            "compact-soft": dict(soft=True, accroche=False),
            "compact-ctx": dict(soft=False, accroche=True),
            "compact-soft-ctx": dict(soft=True, accroche=True),
        }
        for tpl, exp in expectations.items():
            out = str(OUT / f"_preset_{tpl}.pdf")
            try:
                render_cv(base, cv, out, template=tpl)
                pages = count_pdf_pages(out)
                text = _sections(out)
                has_soft = "SAVOIR-ÊTRE" in text.upper()
                has_accroche = "recherche d'un stage de fin" in text
                print(
                    f"\n  [{tpl}] pages={pages} soft={has_soft} accroche={has_accroche}"
                )
                self.assertEqual(pages, 1, f"{tpl} doit tenir sur 1 page")
                self.assertEqual(
                    has_soft, exp["soft"], f"{tpl} : soft attendu={exp['soft']}"
                )
                self.assertEqual(
                    has_accroche,
                    exp["accroche"],
                    f"{tpl} : accroche attendu={exp['accroche']}",
                )
            finally:
                Path(out).unlink(missing_ok=True)
        print("  ✓ 4 presets rendent sur 1 page ; baseline SANS soft ni accroche")


# ---------------------------------------------------------------------------
# 2. Soft skills : section affichée + ligne tronquée à max_soft_skills_line_chars
# ---------------------------------------------------------------------------


class TestSoftSkills(unittest.TestCase):
    def test_soft_line_truncated_to_its_own_limit(self):
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import _build_render_input

        prof = get_profile("compact-soft")
        long_soft = [
            "travail d'équipe",
            "autonomie",
            "rigueur",
            "communication",
            "esprit d'analyse",
            "curiosité technique",
            "adaptabilité",
        ]
        base = _load_base()
        cv = _demo_cv(soft_skills=long_soft)
        data = _build_render_input(base, cv, prof)
        line = ", ".join(data["soft_skills"])

        print(
            f"\n  Entrée soft ({len(', '.join(long_soft))} chars) : {', '.join(long_soft)}"
        )
        print(
            f"  Ligne rendue ({len(line)} chars ≤ {prof.max_soft_skills_line_chars}) : {line}"
        )
        self.assertLessEqual(len(line), prof.max_soft_skills_line_chars)
        self.assertEqual(
            data["soft_skills"],
            long_soft[: len(data["soft_skills"])],
            "préfixe : ordre préservé, pas de reformulation",
        )
        self.assertGreater(len(data["soft_skills"]), 0)
        print("  ✓ soft skills tronqués à leur propre limite, ordre préservé")


# ---------------------------------------------------------------------------
# 3. Accroche FIXE : affichée telle quelle ; prompt B SANS consigne focus
# ---------------------------------------------------------------------------


class TestAccrocheFixe(unittest.TestCase):
    def test_fixe_displayed_and_no_focus_in_prompt(self):
        from jobtool.documents.cv.generate import _build_system_prompt_b
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import _resolve_accroche, accroche_is_adaptable

        prof = get_profile("compact-ctx")
        texte = "Étudiant en data science, à la recherche d'un stage de fin d'études."
        base = _with_profile(_load_base(), accroche=texte)

        # accroche fixe => PAS adaptable => pas de focus demandé
        self.assertFalse(accroche_is_adaptable(base, prof))
        prompt_b = _build_system_prompt_b(
            prof, request_focus=accroche_is_adaptable(base, prof)
        )
        self.assertNotIn(
            "focus",
            prompt_b.lower(),
            "le prompt B ne doit PAS mentionner focus en mode accroche fixe",
        )

        # résolue telle quelle (le focus est ignoré)
        resolved = _resolve_accroche(base, prof, focus="peu importe")
        self.assertEqual(resolved, texte)
        print(f"\n  Accroche fixe résolue : {resolved!r}")
        print("  ✓ accroche fixe affichée telle quelle ; prompt B SANS consigne focus")


# ---------------------------------------------------------------------------
# 4. Accroche ADAPTABLE : prompt B AVEC focus ; template rempli, reste figé
# ---------------------------------------------------------------------------


class TestAccrocheAdaptable(unittest.TestCase):
    def test_adaptable_focus_in_prompt_and_template_filled(self):
        from jobtool.documents.cv.generate import (
            _build_system_prompt_b,
            _build_user_prompt_b,
        )
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import _resolve_accroche, accroche_is_adaptable

        prof = get_profile("compact-ctx")
        template = "Étudiant en informatique, intéressé par {focus}, à la recherche d'un stage."
        base = _with_profile(_load_base(), accroche_template=template)

        # template + show_accroche => adaptable => focus demandé
        self.assertTrue(accroche_is_adaptable(base, prof))
        prompt_b = _build_system_prompt_b(prof, request_focus=True)
        self.assertIn(
            "focus",
            prompt_b.lower(),
            "le prompt B DOIT demander un focus en mode adaptable",
        )
        user_b = _build_user_prompt_b(base, "offre HPC", accroche_template=template)
        self.assertIn(
            template, user_b, "le prompt user B doit montrer le template à compléter"
        )

        # remplissage du trou : reste de la phrase inchangé
        resolved = _resolve_accroche(base, prof, focus="le calcul haute performance")
        self.assertEqual(
            resolved,
            "Étudiant en informatique, intéressé par le calcul haute performance, à la recherche d'un stage.",
        )
        self.assertNotIn("{focus}", resolved)
        print(f"\n  Template  : {template}")
        print(f"  Rempli   : {resolved}")
        print(
            "  ✓ accroche adaptable : prompt B demande le focus ; template rempli, reste figé"
        )

    def test_adaptable_end_to_end_via_stub(self):
        """generate (stub) : le focus de l'appel B traverse remplissage -> rendu."""
        from jobtool.core.rendering.pdf_checks import count_pdf_pages
        from jobtool.documents.cv.generate import generate_tailored_cv_checked
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import render_cv
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredExperience,
            TailoredExperiencesOutput,
            TailoredSkillsOutput,
        )

        template = "Étudiant, intéressé par {focus}, en recherche de stage."
        base = _with_profile(_load_base(), accroche_template=template)

        # Réservoir A : 2 stages épinglés + 2 projets (valide pour base.example).
        result_a = TailoredExperiencesOutput(
            experiences=[
                TailoredExperience(
                    source_experience_id="stage-backend-finup-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.1",
                            text="Endpoints FastAPI + PostgreSQL.",
                        )
                    ],
                ),
                TailoredExperience(
                    source_experience_id="stage-decouverte-mairie-2023",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-decouverte-mairie-2023.2",
                            text="Nettoyage de données en Python.",
                        )
                    ],
                ),
                TailoredExperience(
                    source_experience_id="projet-ecole-systeme-reservation-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="projet-ecole-systeme-reservation-2024.1",
                            text="Architecture backend FastAPI.",
                        )
                    ],
                ),
                TailoredExperience(
                    source_experience_id="projet-ecole-ml-prediction-2023",
                    bullets=[
                        TailoredBullet(
                            source_id="projet-ecole-ml-prediction-2023.2",
                            text="Comparaison de modèles scikit-learn.",
                        )
                    ],
                ),
            ]
        )
        result_b = TailoredSkillsOutput(
            hard_skills=["Python", "FastAPI"],
            soft_skills=["rigueur"],
            interests=["Course à pied"],
            focus="le calcul haute performance",
        )

        returns = iter([result_a, result_b])
        with (
            patch(
                "jobtool.core.llm.correction_loop.call_structured",
                side_effect=lambda *a, **k: next(returns),
            ),
            patch(
                "jobtool.documents.cv.generate.make_client",
                side_effect=lambda: object(),
            ),
        ):
            cv, viol, over = generate_tailored_cv_checked(
                base,
                "offre HPC",
                max_retries=0,
                profile=get_profile("compact-ctx"),
                template="compact-ctx",
            )

        self.assertEqual(
            cv.focus,
            "le calcul haute performance",
            "le focus de l'appel B doit survivre au remplissage",
        )
        out = str(OUT / "_adaptable_e2e.pdf")
        try:
            render_cv(base, cv, out, template="compact-ctx")
            self.assertEqual(count_pdf_pages(out), 1)
            text = _sections(out)
            self.assertIn("calcul haute performance", text)
            self.assertNotIn("{focus}", text)
        finally:
            Path(out).unlink(missing_ok=True)
        print(
            "\n  ✓ end-to-end stub : focus B -> CV -> accroche remplie au rendu (1 page)"
        )


# ---------------------------------------------------------------------------
# 5. Remplissage : template chargé (soft+ctx) => MOINS de bullets que baseline
# ---------------------------------------------------------------------------


class TestFillAdaptsToLoad(unittest.TestCase):
    def test_softctx_has_fewer_or_equal_bullets(self):
        from jobtool.documents.cv.fill import fill_to_one_page
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        base = _with_profile(
            _load_base(),
            accroche="Étudiant en développement logiciel, à la recherche d'un stage de fin d'études.",
        )
        # Réservoir = toutes les expériences de la base, tous leurs bullets.
        reservoir = TailoredCV(
            experiences=[
                TailoredExperience(
                    source_experience_id=e.id,
                    bullets=[
                        TailoredBullet(source_id=a.id, text=a.text.strip())
                        for a in e.achievements
                    ],
                )
                for e in base.experiences
            ],
            hard_skills=list(base.hard_skills),
            soft_skills=list(base.soft_skills),
            interests=list(base.interests),
            focus="le calcul haute performance",
        )

        base_cv, base_rep = fill_to_one_page(
            base, reservoir, profile=get_profile("compact"), template="compact"
        )
        load_cv, load_rep = fill_to_one_page(
            base,
            reservoir,
            profile=get_profile("compact-soft-ctx"),
            template="compact-soft-ctx",
        )

        print(
            f"\n  baseline      : {base_rep.final_entries} entrées / {base_rep.final_bullets} bullets "
            f"({base_rep.renders} rendus)"
        )
        print(
            f"  soft + accroche : {load_rep.final_entries} entrées / {load_rep.final_bullets} bullets "
            f"({load_rep.renders} rendus)"
        )
        self.assertLessEqual(
            load_rep.final_bullets,
            base_rep.final_bullets,
            "le template chargé doit contenir au plus autant de bullets",
        )
        # bornes de coût : linéaire (plancher + bullets + fenêtre post-débordement)
        from jobtool.documents.cv.fill import OVERFLOW_PROBE_WINDOW

        self.assertLessEqual(
            load_rep.renders, load_rep.final_bullets + OVERFLOW_PROBE_WINDOW + 2
        )
        print(
            "  ✓ remplissage s'adapte : template chargé => moins (ou autant) de bullets, coût linéaire"
        )


# ---------------------------------------------------------------------------
# 6. Cas limites de config : 3 erreurs déterministes
# ---------------------------------------------------------------------------


class TestConfigErrors(unittest.TestCase):
    def test_show_accroche_without_any_accroche(self):
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import RenderError, validate_accroche_config

        base = _load_base()  # ni accroche ni template
        with self.assertRaises(RenderError) as ctx:
            validate_accroche_config(base, get_profile("compact-ctx"))
        print(f"\n  [cas 1] {ctx.exception}")
        self.assertIn("ni", str(ctx.exception).lower())
        print("  ✓ show_accroche sans accroche ni template => erreur claire")

    def test_template_without_focus_hole(self):
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import RenderError, validate_accroche_config

        base = _with_profile(_load_base(), accroche_template="Phrase sans trou.")
        with self.assertRaises(RenderError) as ctx:
            validate_accroche_config(base, get_profile("compact-ctx"))
        print(f"  [cas 2] {ctx.exception}")
        self.assertIn("{focus}", str(ctx.exception))
        print("  ✓ template sans {focus} => erreur claire")

    def test_both_present_template_wins(self):
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import _resolve_accroche, accroche_is_adaptable

        base = _with_profile(
            _load_base(),
            accroche="ACCROCHE FIXE.",
            accroche_template="Adaptable : {focus}.",
        )
        prof = get_profile("compact-ctx")
        self.assertTrue(
            accroche_is_adaptable(base, prof),
            "les deux présents => règle : template prioritaire",
        )
        resolved = _resolve_accroche(base, prof, focus="HPC")
        self.assertEqual(resolved, "Adaptable : HPC.")
        self.assertNotIn("ACCROCHE FIXE", resolved)
        print(
            f"\n  [cas 3] accroche ET template présents -> template prioritaire : {resolved!r}"
        )
        print("  ✓ règle déterministe appliquée (template prioritaire si show_accroche)")
