"""Preuves sans clé API des deux changements (troncage + deux appels LLM).

Aucune variable d'environnement requise. Aucun appel réseau. Le paquet jobtool est installé
en editable (pas de sys.path.insert) et mistralai est une vraie dépendance (pas de stub).
"""

from __future__ import annotations

import py_compile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 1. py_compile sur les fichiers modifiés
# ---------------------------------------------------------------------------


class TestCompile(unittest.TestCase):
    TARGETS = [
        "src/jobtool/documents/cv/schema.py",
        "src/jobtool/documents/cv/budget.py",
        "src/jobtool/documents/cv/render.py",
        "src/jobtool/documents/cv/generate.py",
    ]

    def test_compile(self):
        for rel in self.TARGETS:
            path = str(ROOT / rel)
            with self.subTest(file=rel):
                py_compile.compile(path, doraise=True)
                print(f"  ✓ compile {rel}")


# ---------------------------------------------------------------------------
# 2. Imports OK
# ---------------------------------------------------------------------------


class TestImports(unittest.TestCase):
    def test_schema_exports(self):
        from jobtool.documents.cv.schema import (  # noqa: F401
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
            TailoredExperiencesOutput,
            TailoredSkillsOutput,
        )

        print(
            "  ✓ tous les schémas importés (TailoredExperiencesOutput, TailoredSkillsOutput)"
        )

    def test_render_exports(self):
        from jobtool.documents.cv.render import _truncate_skills_line, render_cv  # noqa: F401

        print("  ✓ render._truncate_skills_line importé")

    def test_generate_exports(self):
        from jobtool.documents.cv.generate import (  # noqa: F401
            _check_call_a,
            _check_call_b,
            generate_tailored_cv_checked,
        )

        print("  ✓ generate._check_call_a / _check_call_b importés")

    def test_fill_exports(self):
        from jobtool.documents.cv.fill import FillReport, fill_to_one_page  # noqa: F401

        print("  ✓ fill.fill_to_one_page / FillReport importés")


# ---------------------------------------------------------------------------
# 3. Troncage déterministe
# ---------------------------------------------------------------------------


class TestTruncation(unittest.TestCase):
    def test_truncation_order_and_fit(self):
        from jobtool.documents.cv.render import _truncate_skills_line

        # 15 éléments dont la jointure dépasse 100 chars
        items = [
            "Python",
            "FastAPI",
            "PostgreSQL",
            "Docker",
            "GitHub Actions",
            "React",
            "TypeScript",
            "scikit-learn",
            "pandas",
            "SQL",
            "Java",
            "C",
            "JavaScript",
            "R",
            "Git",
        ]
        full_line = ", ".join(items)
        max_chars = 100
        self.assertGreater(
            len(full_line),
            max_chars,
            "Le test suppose que la liste complète dépasse la limite",
        )

        result = _truncate_skills_line(items, max_chars)

        result_line = ", ".join(result)
        print(f"\n  Entrée  ({len(full_line)} chars) : {full_line}")
        print(f"  Tronqué ({len(result_line)} chars) : {result_line}")

        self.assertLessEqual(
            len(result_line),
            max_chars,
            f"Ligne tronquée dépasse la limite : {len(result_line)} > {max_chars}",
        )
        self.assertGreater(
            len(result), 0, "Le troncage ne doit pas produire une liste vide"
        )
        # Préfixe : l'ordre est préservé
        self.assertEqual(
            result,
            items[: len(result)],
            "Le résultat doit être un PRÉFIXE de la liste d'entrée",
        )
        # Vérifier qu'ajouter l'élément suivant dépasserait la limite
        if len(result) < len(items):
            next_candidate = ", ".join(result + [items[len(result)]])
            self.assertGreater(
                len(next_candidate),
                max_chars,
                "L'élément suivant devrait faire dépasser la limite",
            )
        print("  ✓ troncage : préfixe correct, ordre préservé, longueur ≤ max")

    def test_no_truncation_when_fits(self):
        from jobtool.documents.cv.render import _truncate_skills_line

        items = ["Python", "Docker"]
        result = _truncate_skills_line(items, 100)
        self.assertEqual(result, items)
        print("  ✓ troncage : liste courte conservée intacte")

    def test_empty_list(self):
        from jobtool.documents.cv.render import _truncate_skills_line

        self.assertEqual(_truncate_skills_line([], 100), [])
        print("  ✓ troncage : liste vide → liste vide")


# ---------------------------------------------------------------------------
# 4. check_budget : plus de vérif longueur de ligne de compétences
# ---------------------------------------------------------------------------


class TestBudgetNoSkillsLineCheck(unittest.TestCase):
    def _make_base_and_cv(self):
        """Base et CV minimaux valides pour check_budget."""
        from jobtool.core.experience_base.models import (
            Achievement,
            Experience,
            ExperienceBase,
            ExperienceType,
            Profile,
        )
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        ach = Achievement(id="p1.1", text="A" * 50)
        exp = Experience(
            id="p1",
            type=ExperienceType.school_project,
            title="Projet test",
            organization="École",
            start="2024-01",
            end="2024-06",
            achievements=[ach],
        )
        base = ExperienceBase(
            profile=Profile(name="Test", email="t@t.com"),
            hard_skills=["Python", "Docker"],
            soft_skills=["rigueur"],
            interests=["Veille"],
            experiences=[exp],
        )
        bullet = TailoredBullet(source_id="p1.1", text="A" * 50)
        tailored_exp = TailoredExperience(source_experience_id="p1", bullets=[bullet])
        cv = TailoredCV(
            experiences=[tailored_exp],
            hard_skills=["Python"],  # ligne courte, dans les clous
            soft_skills=["rigueur"],
            interests=["Veille"],
        )
        return base, cv, DEFAULT_PROFILE

    def test_no_skills_line_violation_when_line_long(self):
        """check_budget ne doit plus signaler 'ligne trop longue' pour les compétences."""
        from jobtool.documents.cv.budget import check_budget
        from jobtool.documents.cv.schema import TailoredCV

        base, cv, profile = self._make_base_and_cv()
        # On injecte des listes longues (seraient jadis violées)
        cv = TailoredCV(
            experiences=cv.experiences,
            hard_skills=[
                "Python",
                "Docker",
                "FastAPI",
                "PostgreSQL",
                "React",
                "TypeScript",
                "scikit-learn",
                "pandas",
            ],  # 8 items ≤ max_hard_skills=12
            soft_skills=["rigueur", "autonomie", "communication"],
            interests=["Veille technologique open source", "Course à pied", "Échecs"],
        )
        overruns = check_budget(base, cv, profile)
        skills_line_violations = [
            o for o in overruns if "ligne" in o and "trop longue" in o
        ]
        print(f"\n  overruns check_budget : {overruns}")
        self.assertEqual(
            skills_line_violations,
            [],
            f"Aucune violation 'ligne trop longue' attendue, reçu : {skills_line_violations}",
        )
        print("  ✓ check_budget : aucune violation 'ligne de compétences trop longue'")

    def test_no_global_bullet_budget_violation(self):
        """check_budget n'émet PLUS de violation 'X bullets pour Y entrées, max Z'.

        Le remplissage mesuré (PDF sur 1 page) est désormais la vraie borne du nombre
        total de bullets ; une table de budget global la contredirait.
        """
        from jobtool.core.experience_base.models import (
            Achievement,
            Experience,
            ExperienceBase,
            ExperienceType,
            Profile,
        )
        from jobtool.documents.cv.budget import check_budget
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        profile = DEFAULT_PROFILE
        # 4 entrées (2 projets) très chargées en bullets : jadis bullet_budget[4]=26
        # aurait pu être dépassé ; aujourd'hui aucune violation de budget global.
        exps, tailored = [], []
        specs = [
            ("stageA", ExperienceType.internship, True),
            ("stageB", ExperienceType.internship, True),
            ("projA", ExperienceType.school_project, False),
            ("projB", ExperienceType.personal_project, False),
        ]
        for eid, etype, pin in specs:
            achs = [
                Achievement(id=f"{eid}.{k}", text=f"réalisation {k}") for k in range(5)
            ]
            exps.append(
                Experience(
                    id=eid,
                    type=etype,
                    title="T",
                    organization="O",
                    start="2024-01",
                    end="2024-06",
                    achievements=achs,
                    pinned=pin,
                )
            )
            tailored.append(
                TailoredExperience(
                    source_experience_id=eid,
                    bullets=[
                        TailoredBullet(source_id=f"{eid}.{k}", text=f"réalisation {k}")
                        for k in range(5)
                    ],
                )
            )
        base = ExperienceBase(
            profile=Profile(name="T", email="t@t.com"), experiences=exps
        )
        cv = TailoredCV(experiences=tailored)  # 4 entrées × 5 bullets = 20 bullets

        overruns = check_budget(base, cv, profile)
        global_budget = [o for o in overruns if "bullets pour" in o and "entrées" in o]
        print(f"\n  overruns (20 bullets / 4 entrées) : {overruns}")
        self.assertEqual(
            global_budget,
            [],
            f"Aucune violation de budget global attendue, reçu : {global_budget}",
        )
        print("  ✓ check_budget : aucune violation 'budget global de bullets'")

    def test_budget_still_checks_bullets(self):
        """check_budget vérifie encore les bullets (budget + longueur)."""
        from jobtool.core.experience_base.models import (
            Achievement,
            Experience,
            ExperienceBase,
            ExperienceType,
            Profile,
        )
        from jobtool.documents.cv.budget import check_budget
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        profile = DEFAULT_PROFILE
        # Bullet trop longue
        ach = Achievement(id="q1.1", text="X" * 200)
        exp = Experience(
            id="q1",
            type=ExperienceType.school_project,
            title="P",
            organization="O",
            start="2024-01",
            end="2024-06",
            achievements=[ach],
        )
        base = ExperienceBase(
            profile=Profile(name="T", email="t@t.com"),
            experiences=[exp],
        )
        bullet = TailoredBullet(source_id="q1.1", text="X" * 200)
        cv = TailoredCV(
            experiences=[
                TailoredExperience(source_experience_id="q1", bullets=[bullet])
            ],
        )
        overruns = check_budget(base, cv, profile)
        bullet_violations = [o for o in overruns if "bullet trop longue" in o]
        self.assertGreater(
            len(bullet_violations), 0, "Bullet trop longue doit toujours être détectée"
        )
        print(
            f"  ✓ check_budget : bullet trop longue toujours détectée : {bullet_violations[0]}"
        )


# ---------------------------------------------------------------------------
# 5. Deux appels distincts avec stub client
# ---------------------------------------------------------------------------


class TestTwoCallsStub(unittest.TestCase):
    """Vérifie que generate_tailored_cv_checked fait 2 appels indépendants et assemble."""

    def _build_fixtures(self):
        """Base valide + sorties A/B valides."""
        import yaml

        from jobtool.core.experience_base.models import ExperienceBase

        with open(ROOT / "data" / "base.example.yaml", encoding="utf-8") as f:
            base = ExperienceBase.model_validate(yaml.safe_load(f))

        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredExperience,
            TailoredExperiencesOutput,
            TailoredSkillsOutput,
        )

        # Sortie A : deux expériences épinglées (stages obligatoires) + 1 projet
        result_a = TailoredExperiencesOutput(
            experiences=[
                TailoredExperience(
                    source_experience_id="stage-backend-finup-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.1",
                            text="Développé cinq endpoints REST FastAPI intégrés à PostgreSQL.",
                        ),
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.2",
                            text="Réduit de 40 % la latence d'un endpoint via des index ciblés.",
                        ),
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.3",
                            text="Porté la couverture de tests pytest de 55 % à 88 %.",
                        ),
                    ],
                ),
                TailoredExperience(
                    source_experience_id="stage-decouverte-mairie-2023",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-decouverte-mairie-2023.2",
                            text="Automatisé en Python le nettoyage d'un fichier de données hebdomadaire.",
                        ),
                    ],
                ),
                TailoredExperience(
                    source_experience_id="projet-ecole-systeme-reservation-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="projet-ecole-systeme-reservation-2024.1",
                            text="Conçu l'architecture backend FastAPI + PostgreSQL du projet.",
                        ),
                        TailoredBullet(
                            source_id="projet-ecole-systeme-reservation-2024.4",
                            text="Mis en place Docker Compose et la pipeline GitHub Actions.",
                        ),
                    ],
                ),
                TailoredExperience(
                    source_experience_id="projet-ecole-ml-prediction-2023",
                    bullets=[
                        TailoredBullet(
                            source_id="projet-ecole-ml-prediction-2023.2",
                            text="Comparé trois modèles scikit-learn, le meilleur à MAE -22% vs baseline.",
                        ),
                    ],
                ),
            ]
        )

        # Sortie B : sélection dans les listes maîtres, ordonnée par pertinence
        result_b = TailoredSkillsOutput(
            hard_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Git"],
            soft_skills=["rigueur", "travail d'équipe", "autonomie"],
            interests=["Veille technologique open source", "Course à pied"],
        )

        return base, result_a, result_b

    def test_two_independent_calls_and_assembly(self):
        """Le stub renvoie A puis B ; generate assemble un TailoredCV valide."""
        from jobtool.core.traceability import check_traceability
        from jobtool.documents.cv.budget import check_budget
        from jobtool.documents.cv.generate import generate_tailored_cv_checked
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE
        from jobtool.documents.cv.schema import TailoredCV

        base, result_a, result_b = self._build_fixtures()

        call_log: list[str] = []

        # call_structured est importé dans correction_loop — on patche là-bas.
        call_returns = iter([result_a, result_b])

        def fake_call_structured(client, messages, response_format, **kwargs):
            result = next(call_returns)
            label = (
                "A" if "TailoredExperiencesOutput" in response_format.__name__ else "B"
            )
            call_log.append(label)
            print(f"    → stub appel {label} ({response_format.__name__})")
            return result

        def fake_make_client():
            return object()  # client fictif, jamais utilisé directement

        with (
            patch(
                "jobtool.core.llm.correction_loop.call_structured",
                side_effect=fake_call_structured,
            ),
            patch(
                "jobtool.documents.cv.generate.make_client",
                side_effect=fake_make_client,
            ),
        ):
            # template=compact : le remplissage mesure un VRAI rendu Typst (pas d'API).
            cv, violations, overruns = generate_tailored_cv_checked(
                base,
                "Offre test",
                max_retries=0,
                profile=DEFAULT_PROFILE,
                template="compact",
            )

        # Deux appels LLM bien distincts (le remplissage n'appelle PAS le modèle)
        self.assertEqual(call_log, ["A", "B"], f"Attendu [A, B], reçu {call_log}")
        print(f"  ✓ deux appels LLM distincts dans l'ordre A puis B : {call_log}")

        # Résultat : TailoredCV assemblé. Les expériences sont un SOUS-ENSEMBLE du
        # réservoir A (le remplissage coupe ce qui ne tient pas) ; les compétences = B.
        self.assertIsInstance(cv, TailoredCV)
        reservoir_ids = {e.source_experience_id for e in result_a.experiences}
        final_ids = {e.source_experience_id for e in cv.experiences}
        self.assertTrue(
            final_ids <= reservoir_ids,
            "les expériences finales doivent venir du réservoir A",
        )
        self.assertEqual(cv.hard_skills, result_b.hard_skills)
        self.assertEqual(cv.soft_skills, result_b.soft_skills)
        self.assertEqual(cv.interests, result_b.interests)
        print(
            f"  ✓ TailoredCV assemblé : {len(final_ids)} entrées (⊆ réservoir A), compétences de B"
        )

        # Stages épinglés toujours présents après remplissage
        pinned_ids = {e.id for e in base.experiences if e.pinned}
        self.assertTrue(
            pinned_ids <= final_ids,
            f"stages épinglés manquants : {pinned_ids - final_ids}",
        )
        print(f"  ✓ stages épinglés conservés : {sorted(pinned_ids)}")

        # 0 violation sur le CV FINAL rempli
        self.assertEqual(violations, [], f"Violations inattendues : {violations}")
        self.assertEqual(overruns, [], f"Dépassements inattendus : {overruns}")
        post_violations = check_traceability(base, cv)
        post_overruns = check_budget(base, cv, DEFAULT_PROFILE)
        self.assertEqual(
            post_violations, [], f"check_traceability final : {post_violations}"
        )
        self.assertEqual(post_overruns, [], f"check_budget final : {post_overruns}")
        print(
            "  ✓ check_traceability + check_budget : 0 violation sur le CV FINAL rempli"
        )

    def test_check_a_only_sees_experience_constraints(self):
        """_check_call_a ne produit aucune violation pour des skills vides (proxy ok)."""
        from jobtool.documents.cv.generate import _check_call_a
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, result_a, _ = self._build_fixtures()
        issues = _check_call_a(base, result_a, DEFAULT_PROFILE)
        # Toutes les violations portent sur exp/bullets, pas sur skills inventés
        skill_issues = [i for i in issues if "inventé" in str(i)]
        self.assertEqual(
            skill_issues,
            [],
            f"_check_call_a ne doit pas émettre de violation de skills : {skill_issues}",
        )
        print(
            f"  ✓ _check_call_a : {len(issues)} issue(s) totale(s), 0 liée aux skills"
        )

    def test_check_b_only_sees_skills_constraints(self):
        """_check_call_b ne produit aucune violation pour des expériences vides."""
        from jobtool.documents.cv.generate import _check_call_b
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, _, result_b = self._build_fixtures()
        issues = _check_call_b(base, result_b, DEFAULT_PROFILE)
        pinned_issues = [i for i in issues if "épinglé" in str(i) or "projet" in str(i)]
        self.assertEqual(
            pinned_issues,
            [],
            f"_check_call_b ne doit pas émettre de violation exp/pinned : {pinned_issues}",
        )
        self.assertEqual(
            issues,
            [],
            f"_check_call_b attendait 0 issue sur un résultat valide : {issues}",
        )
        print(
            "  ✓ _check_call_b : 0 violation sur une sélection valide, pas de contraintes d'expériences"
        )


# ---------------------------------------------------------------------------
# 6. Rendu PDF (compact + classic) sans API
# ---------------------------------------------------------------------------


class TestRender(unittest.TestCase):
    def _build_cv(self):
        import yaml

        from jobtool.core.experience_base.models import ExperienceBase
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        with open(ROOT / "data" / "base.example.yaml", encoding="utf-8") as f:
            base = ExperienceBase.model_validate(yaml.safe_load(f))

        cv = TailoredCV(
            experiences=[
                TailoredExperience(
                    source_experience_id="stage-backend-finup-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.1",
                            text="Développé cinq endpoints REST FastAPI + PostgreSQL.",
                        ),
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.2",
                            text="Réduit de 40 % la latence via des index ciblés.",
                        ),
                        TailoredBullet(
                            source_id="stage-backend-finup-2024.3",
                            text="Porté la couverture pytest de 55 % à 88 %.",
                        ),
                    ],
                ),
                TailoredExperience(
                    source_experience_id="stage-decouverte-mairie-2023",
                    bullets=[
                        TailoredBullet(
                            source_id="stage-decouverte-mairie-2023.2",
                            text="Automatisé en Python le nettoyage hebdomadaire de données.",
                        ),
                    ],
                ),
                TailoredExperience(
                    source_experience_id="projet-ecole-systeme-reservation-2024",
                    bullets=[
                        TailoredBullet(
                            source_id="projet-ecole-systeme-reservation-2024.1",
                            text="Conçu l'architecture backend FastAPI + PostgreSQL.",
                        ),
                        TailoredBullet(
                            source_id="projet-ecole-systeme-reservation-2024.4",
                            text="Mis en place Docker Compose et GitHub Actions CI.",
                        ),
                    ],
                ),
            ],
            hard_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Git"],
            soft_skills=["rigueur", "travail d'équipe", "autonomie"],
            interests=["Veille technologique open source", "Course à pied"],
        )
        return base, cv

    def _run_render(self, template: str):
        from jobtool.core.rendering.pdf_checks import check_pdf_ats, check_single_page
        from jobtool.documents.cv.render import render_cv

        base, cv = self._build_cv()
        # Le typst snap ne peut pas écrire dans /tmp ; on utilise output/ (projet).
        out_dir = ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        out_path = str(out_dir / f"cv_test_{template}.pdf")
        try:
            render_cv(base, cv, out_path, template=template)
            page_issues = check_single_page(out_path)
            ats_issues = check_pdf_ats(
                out_path, ["Lucas Moreau", "FastAPI", "PostgreSQL"]
            )
            print(f"\n  Rendu {template} : {out_path}")
            print(f"  Pages : {page_issues if page_issues else '1 page OK'}")
            print(f"  ATS : {ats_issues if ats_issues else 'texte extractible OK'}")
            self.assertEqual(page_issues, [], f"Page issue : {page_issues}")
            self.assertEqual(ats_issues, [], f"ATS : chaînes manquantes : {ats_issues}")
        finally:
            Path(out_path).unlink(missing_ok=True)

    def test_render_compact(self):
        self._run_render("compact")
        print("  ✓ rendu compact OK")

    def test_render_classic(self):
        self._run_render("classic")
        print("  ✓ rendu classic OK")

    def test_truncation_applied_in_render(self):
        """Le rendu tronque les lignes de compétences avant d'envoyer à Typst."""
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.documents.cv.render import DEFAULT_TEMPLATE, _build_render_input
        from jobtool.documents.cv.schema import TailoredCV

        base, cv = self._build_cv()
        # On injecte 15 hard skills dans le CV pour dépasser la limite
        cv = TailoredCV(
            experiences=cv.experiences,
            hard_skills=[
                "Python",
                "FastAPI",
                "PostgreSQL",
                "Docker",
                "GitHub Actions",
                "React",
                "TypeScript",
                "scikit-learn",
                "pandas",
                "SQL",
                "Java",
                "C",
                "JavaScript",
                "R",
                "Git",
            ],
            soft_skills=cv.soft_skills,
            interests=cv.interests,
        )
        tmpl_profile = get_profile(DEFAULT_TEMPLATE)
        data = _build_render_input(base, cv, tmpl_profile)
        line = ", ".join(data["hard_skills"])
        self.assertLessEqual(
            len(line),
            tmpl_profile.max_skills_line_chars,
            f"Ligne hard skills dans render_input dépasse la limite : {len(line)}",
        )
        print(
            f"\n  hard_skills tronqués au rendu ({len(line)} chars ≤ {tmpl_profile.max_skills_line_chars}) : {line}"
        )
        print("  ✓ troncage appliqué dans _build_render_input")


# ---------------------------------------------------------------------------
# 7. Remplissage mesuré sur le PDF (cv/fill.py)
# ---------------------------------------------------------------------------


def _synthetic_base(n_stages=3, n_projects=6, bullets_per=5, text_len=95):
    """Base synthétique : n_stages internships ÉPINGLÉS + n_projects projets.

    Chaque expérience a `bullets_per` achievements d'environ `text_len` caractères.
    Utilisée pour piloter finement le remplissage sans dépendre du LLM.
    """
    from jobtool.core.experience_base.models import (
        Achievement,
        Education,
        Experience,
        ExperienceBase,
        ExperienceType,
        Language,
        Profile,
    )

    def _ach(eid, k):
        base_txt = f"{eid} réalisation {k} : "
        pad = "lorem ipsum dolor sit amet consectetur " * 4
        return Achievement(id=f"{eid}.{k}", text=(base_txt + pad)[:text_len])

    exps = []
    for s in range(n_stages):
        eid = f"stage{s}"
        exps.append(
            Experience(
                id=eid,
                type=ExperienceType.internship,
                pinned=True,
                title=f"Stage numéro {s}",
                organization=f"Entreprise {s}",
                start="2024-01",
                end="2024-06",
                achievements=[_ach(eid, k) for k in range(bullets_per)],
            )
        )
    for p in range(n_projects):
        eid = f"proj{p}"
        etype = (
            ExperienceType.school_project
            if p % 2 == 0
            else ExperienceType.personal_project
        )
        exps.append(
            Experience(
                id=eid,
                type=etype,
                pinned=False,
                title=f"Projet numéro {p}",
                organization="INSA",
                start="2023-01",
                end="2023-06",
                achievements=[_ach(eid, k) for k in range(bullets_per)],
            )
        )

    return ExperienceBase(
        profile=Profile(
            name="Test Persona",
            email="test@example.com",
            phone="+33 6 00 00 00 00",
            location="Lyon",
        ),
        education=[
            Education(
                degree="Diplôme d'ingénieur",
                school="INSA Lyon",
                start="2022-09",
                end="2025-09",
            )
        ],
        languages=[
            Language(name="Français", level="Langue maternelle"),
            Language(name="Anglais", level="B2"),
        ],
        hard_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Git"],
        soft_skills=["rigueur", "autonomie"],
        interests=["Course à pied", "Échecs"],
        experiences=exps,
    )


def _reservoir_from_base(base):
    """Réservoir classé = toutes les expériences de la base, tous leurs bullets en ordre."""
    from jobtool.documents.cv.schema import (
        TailoredBullet,
        TailoredCV,
        TailoredExperience,
    )

    exps = []
    for e in base.experiences:
        exps.append(
            TailoredExperience(
                source_experience_id=e.id,
                bullets=[
                    TailoredBullet(source_id=a.id, text=a.text) for a in e.achievements
                ],
            )
        )
    return TailoredCV(
        experiences=exps,
        hard_skills=list(base.hard_skills),
        soft_skills=list(base.soft_skills),
        interests=list(base.interests),
    )


class TestFillRealRender(unittest.TestCase):
    """Remplissage avec VRAI rendu Typst (pas d'API)."""

    def test_fills_and_stops_before_two_pages(self):
        from jobtool.core.rendering.pdf_checks import check_pdf_ats, count_pdf_pages
        from jobtool.documents.cv.fill import fill_to_one_page
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE
        from jobtool.documents.cv.render import render_cv

        base = _synthetic_base(n_stages=3, n_projects=6, bullets_per=5, text_len=95)
        reservoir = _reservoir_from_base(base)
        total_reservoir_bullets = sum(len(e.bullets) for e in reservoir.experiences)

        final_cv, report = fill_to_one_page(
            base, reservoir, profile=DEFAULT_PROFILE, template="compact"
        )

        print(
            f"\n  Réservoir : {len(reservoir.experiences)} entrées / "
            f"{total_reservoir_bullets} bullets (3 stages + 6 projets)"
        )
        print(
            f"  Rempli    : {report.final_entries} entrées / {report.final_bullets} bullets"
        )
        print(f"  Rendus Typst effectués : {report.renders}")
        print(
            f"  Arrêt sur page pleine : {report.stopped_on_overflow} ; "
            f"plancher en débordement : {report.floor_overflow}"
        )

        # Le réservoir déborde (45 bullets) : le remplissage DOIT s'arrêter avant 2 pages.
        self.assertTrue(
            report.stopped_on_overflow,
            "avec 45 bullets, le remplissage devrait s'arrêter sur page pleine",
        )
        self.assertLess(
            report.final_bullets,
            total_reservoir_bullets,
            "le remplissage devrait avoir coupé une partie du réservoir",
        )

        # Le PDF FINAL fait bien 1 page (on le rend et on le mesure).
        out_dir = ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        out_path = str(out_dir / "cv_fill_final.pdf")
        try:
            render_cv(base, final_cv, out_path, template="compact")
            pages = count_pdf_pages(out_path)
            missing = check_pdf_ats(out_path, ["Test Persona"])
            print(
                f"  PDF final : {pages} page(s) ; ATS manquants : {missing or 'aucun'}"
            )
            self.assertEqual(
                pages, 1, f"le PDF final devrait faire 1 page, fait {pages}"
            )
            self.assertEqual(missing, [], f"ATS : {missing}")
        finally:
            Path(out_path).unlink(missing_ok=True)

        # Nombre de rendus borné linéairement : ≈ bullets gardés + plancher + 1er
        # débordement + la fenêtre d'exploration post-débordement (OVERFLOW_PROBE_WINDOW).
        from jobtool.documents.cv.fill import OVERFLOW_PROBE_WINDOW

        self.assertLessEqual(
            report.renders,
            report.final_bullets + OVERFLOW_PROBE_WINDOW + 2,
            "le nombre de rendus doit rester linéaire, pas quadratique",
        )
        print(
            f"  ✓ remplissage : s'arrête avant 2 pages, PDF final 1 page, "
            f"{report.renders} rendus (linéaire)"
        )

    def test_floor_invariants_preserved(self):
        """Le résultat ne descend jamais sous 3 stages + min_projects projets + min_bullets."""
        from jobtool.core.experience_base.models import PROJECT_TYPES
        from jobtool.documents.cv.fill import fill_to_one_page
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        profile = DEFAULT_PROFILE
        # text_len max (120) pour saturer la page vite et forcer un arrêt précoce.
        base = _synthetic_base(n_stages=3, n_projects=6, bullets_per=5, text_len=120)
        reservoir = _reservoir_from_base(base)
        final_cv, report = fill_to_one_page(
            base, reservoir, profile=profile, template="compact"
        )

        type_by_id = {e.id: e.type.value for e in base.experiences}
        pinned_ids = {e.id for e in base.experiences if e.pinned}
        final_ids = {e.source_experience_id for e in final_cv.experiences}
        n_projects = sum(
            1
            for e in final_cv.experiences
            if type_by_id.get(e.source_experience_id) in PROJECT_TYPES
        )

        print(
            f"\n  Plancher attendu : 3 stages + {profile.min_projects} projets, "
            f"≥{profile.min_bullets_per_entry} bullet/entrée"
        )
        print(f"  Résultat : {sorted(final_ids)} ({n_projects} projets)")

        self.assertTrue(
            pinned_ids <= final_ids,
            f"stages épinglés manquants : {pinned_ids - final_ids}",
        )
        self.assertGreaterEqual(
            n_projects,
            profile.min_projects,
            f"{n_projects} projets < {profile.min_projects} minimum",
        )
        for e in final_cv.experiences:
            self.assertGreaterEqual(
                len(e.bullets),
                profile.min_bullets_per_entry,
                f"entrée {e.source_experience_id} sous le min de bullets",
            )
        print(
            f"  ✓ plancher respecté : 3 stages + {n_projects} projets, jamais sous le minimum"
        )

    def test_no_displayed_entry_without_bullet(self):
        """Aucune entrée affichée n'a 0 bullet (anti-projet-vide), même après arrêt."""
        from jobtool.documents.cv.fill import fill_to_one_page
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base = _synthetic_base(n_stages=3, n_projects=6, bullets_per=5, text_len=110)
        reservoir = _reservoir_from_base(base)
        final_cv, _ = fill_to_one_page(
            base, reservoir, profile=DEFAULT_PROFILE, template="compact"
        )
        empties = [
            e.source_experience_id for e in final_cv.experiences if not e.bullets
        ]
        self.assertEqual(empties, [], f"entrées affichées sans bullet : {empties}")
        print(
            f"\n  ✓ anti-entrée-vide : {len(final_cv.experiences)} entrées, toutes avec ≥1 bullet"
        )


class TestFillAlgorithmStubbed(unittest.TestCase):
    """Logique du remplissage isolée, avec un compteur de pages DÉTERMINISTE (stub).

    On remplace render_cv (no-op) et count_pdf_pages (1 page tant que total_bullets <=
    LIMIT, sinon 2) pour exercer précisément les branches plancher / anti-projet-vide,
    sans dépendre des métriques de police réelles.
    """

    def _run_with_limit(self, base, reservoir, limit, profile):
        import jobtool.documents.cv.fill as fillmod
        from jobtool.documents.cv.fill import fill_to_one_page

        holder = {"cv": None}

        def fake_render(base_, cv_, out_, template=None, photo=None):
            holder["cv"] = cv_
            return out_

        def fake_count(path):
            n = sum(len(e.bullets) for e in holder["cv"].experiences)
            return 1 if n <= limit else 2

        with (
            patch.object(fillmod, "render_cv", side_effect=fake_render),
            patch.object(fillmod, "count_pdf_pages", side_effect=fake_count),
        ):
            return fill_to_one_page(
                base, reservoir, profile=profile, template="compact"
            )

    def _toy(self):
        """3 stages + 3 projets, 2 bullets chacun (12 bullets, ids contrôlés)."""
        from jobtool.core.experience_base.models import (
            Achievement,
            Experience,
            ExperienceBase,
            ExperienceType,
            Profile,
        )
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        specs = [
            ("stage0", ExperienceType.internship, True),
            ("stage1", ExperienceType.internship, True),
            ("stage2", ExperienceType.internship, True),
            ("proj0", ExperienceType.school_project, False),
            ("proj1", ExperienceType.personal_project, False),
            ("proj2", ExperienceType.school_project, False),
        ]
        exps, res = [], []
        for eid, etype, pin in specs:
            achs = [
                Achievement(id=f"{eid}.{k}", text=f"{eid} bullet {k}") for k in range(2)
            ]
            exps.append(
                Experience(
                    id=eid,
                    type=etype,
                    pinned=pin,
                    title=eid,
                    organization="O",
                    start="2024-01",
                    end="2024-06",
                    achievements=achs,
                )
            )
            res.append(
                TailoredExperience(
                    source_experience_id=eid,
                    bullets=[
                        TailoredBullet(source_id=f"{eid}.{k}", text=f"{eid} bullet {k}")
                        for k in range(2)
                    ],
                )
            )
        base = ExperienceBase(
            profile=Profile(name="T", email="t@t.com"), experiences=exps
        )
        reservoir = TailoredCV(experiences=res)
        return base, reservoir

    def test_anti_empty_project_on_revert(self):
        """Un candidat qui OUVRE un projet et déborde est retiré AVEC son projet."""
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, reservoir = self._toy()

        # Plancher = 3 stages + 2 projets (min_projects=2), 1 bullet chacun = 5 bullets.
        # Ordre des candidats : 2es bullets de stage0,1,2,proj0,proj1 (→10 bullets),
        # PUIS proj2.0 (ouverture d'un 3e projet). LIMIT=10 : tout tient jusqu'à 10,
        # ouvrir proj2 (11e bullet) déborde -> proj2 doit être retiré entièrement.
        final_cv, report = self._run_with_limit(
            base, reservoir, limit=10, profile=DEFAULT_PROFILE
        )

        final_ids = {e.source_experience_id for e in final_cv.experiences}
        print(f"\n  LIMIT=10 ; entrées finales : {sorted(final_ids)}")
        print(f"  arrêt page pleine : {report.stopped_on_overflow}")
        self.assertTrue(report.stopped_on_overflow)
        self.assertNotIn(
            "proj2",
            final_ids,
            "le projet ouvert par le candidat qui déborde doit être retiré",
        )
        empties = [
            e.source_experience_id for e in final_cv.experiences if not e.bullets
        ]
        self.assertEqual(empties, [], f"entrées vides : {empties}")
        print(
            "  ✓ anti-projet-vide : proj2 (ouvert puis débordant) retiré, aucune entrée vide"
        )

    def test_floor_only_when_no_candidate_fits(self):
        """LIMIT au ras du plancher : on garde exactement le plancher, rien de plus."""
        from jobtool.core.experience_base.models import PROJECT_TYPES
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, reservoir = self._toy()
        # Plancher = 5 bullets. LIMIT=5 : aucun ajout ne tient (6 > 5).
        final_cv, report = self._run_with_limit(
            base, reservoir, limit=5, profile=DEFAULT_PROFILE
        )

        type_by_id = {e.id: e.type.value for e in base.experiences}
        n_projects = sum(
            1
            for e in final_cv.experiences
            if type_by_id.get(e.source_experience_id) in PROJECT_TYPES
        )
        total = sum(len(e.bullets) for e in final_cv.experiences)
        print(
            f"\n  LIMIT=5 ; entrées={len(final_cv.experiences)}, bullets={total}, projets={n_projects}"
        )
        self.assertEqual(total, 5, "seul le plancher (5 bullets) doit rester")
        self.assertEqual(n_projects, DEFAULT_PROFILE.min_projects)
        self.assertFalse(report.floor_overflow, "le plancher tient (LIMIT=5)")
        print("  ✓ plancher seul conservé quand aucun candidat ne tient")

    def test_floor_overflow_flagged(self):
        """LIMIT sous le plancher : floor_overflow=True, on n'ajoute rien."""
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, reservoir = self._toy()
        # Plancher = 5 bullets > LIMIT=4 -> déborde déjà.
        final_cv, report = self._run_with_limit(
            base, reservoir, limit=4, profile=DEFAULT_PROFILE
        )
        total = sum(len(e.bullets) for e in final_cv.experiences)
        print(f"\n  LIMIT=4 ; floor_overflow={report.floor_overflow}, bullets={total}")
        self.assertTrue(
            report.floor_overflow, "le plancher déborde et doit être signalé"
        )
        self.assertEqual(
            total,
            5,
            "on conserve le plancher tel quel (pdf_checks signalera les 2 pages)",
        )
        print(
            "  ✓ plancher en débordement signalé (floor_overflow), réservoir non amputé"
        )

    # --- Remplissage du creux : ne plus s'arrêter au 1er débordement ----------

    def _toy_heavy_light(self):
        """3 stages + 2 projets plancher + projH (LOURD) + projL (LÉGER), 1 bullet chacun.

        Modèle de hauteur : un candidat « lourd » (ouverture d'une entrée à fort poids)
        déborde, alors qu'un candidat « léger » juste après comblerait le creux.
        """
        from jobtool.core.experience_base.models import (
            Achievement,
            Experience,
            ExperienceBase,
            ExperienceType,
            Profile,
        )
        from jobtool.documents.cv.schema import (
            TailoredBullet,
            TailoredCV,
            TailoredExperience,
        )

        specs = [
            ("stage0", ExperienceType.internship, True),
            ("stage1", ExperienceType.internship, True),
            ("stage2", ExperienceType.internship, True),
            ("proj0", ExperienceType.school_project, False),
            ("proj1", ExperienceType.personal_project, False),
            ("projH", ExperienceType.school_project, False),  # LOURD, mieux classé
            ("projL", ExperienceType.personal_project, False),
        ]  # LÉGER, juste derrière
        exps, res = [], []
        for eid, etype, pin in specs:
            ach = Achievement(id=f"{eid}.0", text=f"{eid} bullet 0")
            exps.append(
                Experience(
                    id=eid,
                    type=etype,
                    pinned=pin,
                    title=eid,
                    organization="O",
                    start="2024-01",
                    end="2024-06",
                    achievements=[ach],
                )
            )
            res.append(
                TailoredExperience(
                    source_experience_id=eid,
                    bullets=[
                        TailoredBullet(source_id=f"{eid}.0", text=f"{eid} bullet 0")
                    ],
                )
            )
        base = ExperienceBase(
            profile=Profile(name="T", email="t@t.com"), experiences=exps
        )
        return base, TailoredCV(experiences=res)

    def _run_with_height(self, base, reservoir, limit, profile, weights, window=None):
        """Stub hauteur : page=2 si Σ(poids_entrée + nb_bullets) > limit. `window` patche
        OVERFLOW_PROBE_WINDOW (0 => ancien comportement : arrêt au 1er débordement)."""
        import contextlib

        import jobtool.documents.cv.fill as fillmod
        from jobtool.documents.cv.fill import fill_to_one_page

        holder = {"cv": None}

        def fake_render(b, cv, out, template=None, photo=None):
            holder["cv"] = cv
            return out

        def fake_count(path):
            cv = holder["cv"]
            h = sum(
                weights.get(e.source_experience_id, 2) + len(e.bullets)
                for e in cv.experiences
            )
            return 1 if h <= limit else 2

        cms = [
            patch.object(fillmod, "render_cv", side_effect=fake_render),
            patch.object(fillmod, "count_pdf_pages", side_effect=fake_count),
        ]
        if window is not None:
            cms.append(patch.object(fillmod, "OVERFLOW_PROBE_WINDOW", window))
        with contextlib.ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            return fill_to_one_page(
                base, reservoir, profile=profile, template="compact"
            )

    def test_gap_filled_after_heavy_overflow(self):
        """Un candidat LOURD déborde ; un candidat LÉGER juste après comble le creux."""
        from jobtool.documents.cv.profiles import DEFAULT_PROFILE

        base, reservoir = self._toy_heavy_light()
        # Plancher = 3 stages + proj0 + proj1, 1 bullet chacun. Hauteur plancher = 5*(2+1)=15.
        # projH ouvre une entrée LOURDE (poids 10 -> +11 = 26 > 20 : déborde).
        # projL ouvre une entrée LÉGÈRE (poids 2 -> +3 = 18 <= 20 : rentre).
        weights = {"projH": 10}  # les autres : poids par défaut 2

        # ANCIEN comportement (fenêtre = 0) : on s'arrête au débordement de projH.
        old_cv, old_rep = self._run_with_height(
            base, reservoir, 20, DEFAULT_PROFILE, weights, window=0
        )
        old_ids = {e.source_experience_id for e in old_cv.experiences}

        # NOUVEAU comportement (fenêtre par défaut) : on saute projH et on prend projL.
        new_cv, new_rep = self._run_with_height(
            base, reservoir, 20, DEFAULT_PROFILE, weights
        )
        new_ids = {e.source_experience_id for e in new_cv.experiences}

        print(
            f"\n  ANCIEN (fenêtre=0) : {sorted(old_ids)}  "
            f"({old_rep.final_bullets} bullets, {old_rep.renders} rendus)"
        )
        print(
            f"  NOUVEAU            : {sorted(new_ids)}  "
            f"({new_rep.final_bullets} bullets, {new_rep.renders} rendus ; "
            f"gap_probes={new_rep.gap_probes}, gap_fills={new_rep.gap_fills})"
        )

        # Ancien : projL JAMAIS essayé -> creux laissé (plancher seul).
        self.assertNotIn("projL", old_ids)
        self.assertNotIn("projH", old_ids)
        self.assertEqual(old_rep.final_bullets, 5, "ancien : reste au plancher")

        # Nouveau : projH (lourd) sauté, projL (léger) ajouté -> creux comblé.
        self.assertNotIn("projH", new_ids, "le candidat lourd qui déborde reste exclu")
        self.assertIn(
            "projL", new_ids, "le candidat léger suivant doit combler le creux"
        )
        self.assertEqual(new_rep.final_bullets, 6, "nouveau : +1 bullet (projL)")
        self.assertEqual(new_rep.gap_fills, 1)

        # Invariants : 1 page finale, plancher préservé, aucune entrée vide.
        def weights_h(cv):
            return sum(
                weights.get(e.source_experience_id, 2) + len(e.bullets)
                for e in cv.experiences
            )

        self.assertLessEqual(weights_h(new_cv), 20, "le résultat tient sur 1 page")
        for eid in ("stage0", "stage1", "stage2"):
            self.assertIn(eid, new_ids, "les 3 stages restent")
        self.assertEqual(
            [e.source_experience_id for e in new_cv.experiences if not e.bullets],
            [],
            "aucune entrée affichée sans bullet",
        )
        # Coût borné : un seul rendu de plus que l'ancien pour combler le creux.
        self.assertLessEqual(
            new_rep.renders, old_rep.renders + DEFAULT_PROFILE.max_projects + 3
        )
        print(
            "  ✓ creux comblé par un voisin de classement, 1 page, plancher + anti-vide OK"
        )
