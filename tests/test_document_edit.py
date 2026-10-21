"""Preuves de la RETOUCHE MANUELLE d'un document généré (post-traitement du PDF).

Trois familles de preuves :
  1. Repérage    — le compilateur Typst rend chaque zone éditable avec son id, son texte
                   et sa boîte ; les ids de puces sont les ids de TRAÇABILITÉ existants.
  2. Édition     — appliquer/retirer une retouche change le rendu et rien d'autre ;
                   l'état d'origine reste récupérable champ par champ.
  3. Contrôles   — un document retouché repasse par les MÊMES bornes dures que généré
                   (une page, budget, traçabilité, extractibilité ATS).

Aucun appel LLM ici : les brouillons sont fabriqués depuis les données d'exemple, ce que
permet précisément le fait qu'un brouillon = render_input + retouches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import YamlExperienceBase
from jobtool.documents.drafts import store
from jobtool.documents.drafts.checks import ats_targets, rebuild_tailored_cv
from jobtool.documents.drafts.models import DocumentDraft
from jobtool.documents.drafts.overrides import apply_overrides
from jobtool.services.document_edit_service import DocumentEditService

BASE_YAML = Path("data/base.example.yaml")


# --- fabrique de brouillons (sans LLM) --------------------------------------


@pytest.fixture
def cv_render_input(example_base, example_cv):
    """`render_input` d'un CV d'exemple, tel que produit par la génération."""
    from jobtool.documents.cv.profiles import get_profile
    from jobtool.documents.cv.render import build_render_input

    return build_render_input(example_base, example_cv, get_profile("compact"))


@pytest.fixture
def edit_service(typst_out_dir):
    """Service d'édition sur une base de brouillons JETABLE, câblé sur la base d'exemple."""
    return DocumentEditService(
        base_repo=YamlExperienceBase(BASE_YAML),
        drafts_db=typst_out_dir / "drafts.db",
        work_root=typst_out_dir / "docs",
        profile_key="",
    )


@pytest.fixture
def seeded_cv(edit_service, cv_render_input):
    """Enregistre un brouillon de CV prêt à retoucher ; renvoie son doc_id.

    On passe par le service pour relever les textes d'origine (donc par le compilateur),
    exactement comme le fait une vraie création — seule la génération LLM est court-circuitée.
    """
    doc_id = "abcdef0123456789"
    timestamp = store.now_iso()
    draft = DocumentDraft(
        doc_id=doc_id,
        offer_id="offre-test",
        kind="cv",
        template="compact",
        render_input=cv_render_input,
        created_at=timestamp,
        updated_at=timestamp,
    )
    originals = edit_service._compile(
        draft, cv_render_input, edit_service._work_dir(doc_id) / "_original"
    )
    draft = draft.model_copy(
        update={"original_texts": {box.id: box.text for box in originals.fields}}
    )
    conn = store.connect(edit_service._drafts_db)
    try:
        store.save_draft(conn, LOCAL_USER_ID, draft, profile_key="")
    finally:
        conn.close()
    return doc_id


# --- 1. Repérage des zones dans le PDF --------------------------------------


class TestFieldLocation:
    """Le COMPILATEUR dit où est chaque phrase — le front ne devine rien."""

    def test_bullet_ids_are_traceability_ids(
        self, edit_service, seeded_cv, example_cv, subtests
    ):
        """Chaque puce est repérée par l'id de l'achievement qu'elle reformule."""
        view = edit_service.get_document(seeded_cv)
        field_ids = {f.id for f in view.fields}

        expected = {
            bullet.source_id for exp in example_cv.experiences for bullet in exp.bullets
        }
        with subtests.test("toutes les puces sont repérées"):
            assert expected <= field_ids, sorted(expected - field_ids)

        with subtests.test("les lignes de compétences aussi"):
            assert {"hard_skills", "interests"} <= field_ids

    def test_boxes_are_within_the_page(self, edit_service, seeded_cv):
        """Les boîtes sont exprimées en % de page et tiennent dans la page."""
        view = edit_service.get_document(seeded_cv)
        assert view.fields, "aucune zone éditable relevée"
        for f in view.fields:
            assert 0 <= f.left < 100, f.id
            assert 0 <= f.top < 100, f.id
            assert 0 < f.width <= 100, f.id
            assert 0 < f.height <= 100, f.id
            assert f.left + f.width <= 100.5, f.id

    def test_field_text_matches_what_is_rendered(self, edit_service, seeded_cv):
        """Le texte remonté par le compilateur est bien celui du PDF (contrôle ATS)."""
        from jobtool.core.rendering.pdf_checks import extract_pdf_text

        view = edit_service.get_document(seeded_cv)
        extracted = " ".join(extract_pdf_text(view.pdf_path).split())
        bullets = [f for f in view.fields if "." in f.id]
        assert bullets, "aucune puce relevée"
        for f in bullets:
            fragment = " ".join(f.text.split()[:6])
            assert fragment in extracted, f"{f.id} : « {fragment} » absent du PDF"

    def test_preview_is_produced_per_page(self, edit_service, seeded_cv):
        """Un aperçu PNG par page, issu de la MÊME compilation que le PDF."""
        view = edit_service.get_document(seeded_cv)
        assert len(view.preview_paths) == view.page_count == 1
        assert Path(view.preview_paths[0]).is_file()
        assert Path(view.pdf_path).is_file()


# --- 2. Application des retouches -------------------------------------------


class TestOverrides:
    """`apply_overrides` est le seul endroit qui sait où vit chaque id."""

    def test_cv_bullet_override(self, cv_render_input):
        target = cv_render_input["experiences"][0]["bullets"][0]["id"]
        data = apply_overrides("cv", cv_render_input, {target: "Texte réécrit."})

        assert data["experiences"][0]["bullets"][0]["text"] == "Texte réécrit."
        # L'original n'est jamais muté : « revenir au texte généré » reste possible.
        assert (
            cv_render_input["experiences"][0]["bullets"][0]["text"] != "Texte réécrit."
        )

    def test_cv_skills_line_round_trips(self, cv_render_input):
        """Une ligne de compétences retouchée se redécoupe sans changer le rendu."""
        line = "Python, Rust, Zig"
        data = apply_overrides("cv", cv_render_input, {"hard_skills": line})
        assert data["hard_skills"] == ["Python", "Rust", "Zig"]
        assert ", ".join(data["hard_skills"]) == line

    def test_unknown_ids_are_ignored(self, cv_render_input):
        """Une retouche orpheline ne doit pas empêcher le document de se recompiler."""
        data = apply_overrides("cv", cv_render_input, {"id-qui-nexiste-plus": "x"})
        assert data == cv_render_input

    def test_letter_override_disables_the_insert(self):
        """Retoucher un paragraphe composé neutralise son insert (sinon il doublonnerait).

        Le champ éditable porte le texte DÉJÀ assemblé par le template : si l'insert
        restait actif, il serait recollé derrière le texte saisi par l'humain.
        """
        data = apply_overrides(
            "letter",
            {
                "paragraphe_vous": "Original.",
                "insert_interet": " Insert.",
                "show_interet": True,
            },
            {"paragraphe_vous": "Original. Insert. Retouché."},
        )
        assert data["paragraphe_vous"] == "Original. Insert. Retouché."
        assert data["show_interet"] is False


# --- 3. Les contrôles sont rejoués sur le texte ÉDITÉ ------------------------


class TestChecksAfterEditing:
    """Une retouche ne crée pas d'angle mort : le code remesure tout."""

    def test_generated_document_is_valid(self, edit_service, seeded_cv):
        view = edit_service.get_document(seeded_cv)
        assert view.checks.pages == 1
        assert view.checks.ok, (
            view.checks.page_violations
            + view.checks.overruns
            + view.checks.violations
            + view.checks.missing_ats
        )

    def test_overlong_bullet_is_caught_by_the_budget(self, edit_service, seeded_cv):
        """Une puce trop longue est signalée — comme pour un texte généré."""
        view = edit_service.get_document(seeded_cv)
        target = next(f for f in view.fields if "." in f.id)

        edited = edit_service.set_fields(
            seeded_cv, {target.id: "Beaucoup trop long. " * 20}
        )
        assert not edited.checks.ok
        assert any("bullet trop longue" in o for o in edited.checks.overruns)

    def test_edit_that_overflows_is_reported_not_truncated(
        self, edit_service, seeded_cv, subtests
    ):
        """Un débordement sur 2 pages est SIGNALÉ (jamais tronqué en silence)."""
        view = edit_service.get_document(seeded_cv)
        target = next(f for f in view.fields if "." in f.id)

        edited = edit_service.set_fields(
            seeded_cv, {target.id: "Retouche démesurée. " * 200}
        )
        with subtests.test("le PDF fait bien 2 pages"):
            assert edited.checks.pages > 1
        with subtests.test("la violation est explicite"):
            assert any("attendu 1" in v for v in edited.checks.page_violations)
        with subtests.test("l'aperçu montre les deux pages"):
            assert edited.page_count == edited.checks.pages

    def test_ats_targets_follow_the_edited_text(self, cv_render_input):
        """Les cibles ATS sont recalculées sur le texte retouché, pas sur l'original."""
        target = cv_render_input["experiences"][0]["bullets"][0]["id"]
        data = apply_overrides(
            "cv", cv_render_input, {target: "Une formulation entièrement nouvelle ici."}
        )
        assert "Une formulation entièrement nouvelle ici." in ats_targets("cv", data)

    def test_render_input_rebuilds_a_tailored_cv(self, cv_render_input, example_cv):
        """Le render_input reste réversible : c'est ce qui permet de rejouer check_budget."""
        rebuilt = rebuild_tailored_cv(cv_render_input)
        assert [e.source_experience_id for e in rebuilt.experiences] == [
            e.source_experience_id for e in example_cv.experiences
        ]
        assert [b.source_id for e in rebuilt.experiences for b in e.bullets] == [
            b.source_id for e in example_cv.experiences for b in e.bullets
        ]


# --- 4. Cycle de vie d'une retouche -----------------------------------------


class TestEditLifecycle:
    def test_edit_then_reset_restores_the_generated_text(
        self, edit_service, seeded_cv, subtests
    ):
        view = edit_service.get_document(seeded_cv)
        target = next(f for f in view.fields if "." in f.id)
        original = target.text

        edited = edit_service.set_fields(seeded_cv, {target.id: "Version manuelle."})
        with subtests.test("la retouche est appliquée et signalée"):
            after = next(f for f in edited.fields if f.id == target.id)
            assert after.text == "Version manuelle."
            assert after.edited
            assert after.original == original
            assert edited.edited_field_ids == [target.id]

        restored = edit_service.reset_fields(seeded_cv, [target.id])
        with subtests.test("le texte généré est récupéré"):
            back = next(f for f in restored.fields if f.id == target.id)
            assert back.text == original
            assert not back.edited
            assert restored.edited_field_ids == []

    def test_reediting_to_the_original_clears_the_override(
        self, edit_service, seeded_cv
    ):
        """Retaper le texte d'origine n'est pas une retouche : le champ redevient neutre."""
        view = edit_service.get_document(seeded_cv)
        target = next(f for f in view.fields if "." in f.id)

        edit_service.set_fields(seeded_cv, {target.id: "Autre chose."})
        back = edit_service.set_fields(seeded_cv, {target.id: target.text})
        assert back.edited_field_ids == []

    def test_edits_survive_a_new_service_instance(self, edit_service, seeded_cv):
        """L'état vit côté serveur : un nouveau client (ou un rechargement) le retrouve.

        C'est la raison d'être du stockage serveur — le navigateur ne détient rien
        d'autoritaire, il réaffiche ce que l'API lui rend.
        """
        target = next(
            f for f in edit_service.get_document(seeded_cv).fields if "." in f.id
        )
        edit_service.set_fields(seeded_cv, {target.id: "Persistée."})

        reborn = DocumentEditService(
            base_repo=YamlExperienceBase(BASE_YAML),
            drafts_db=edit_service._drafts_db,
            work_root=edit_service._work_root,
            profile_key="",
        )
        view = reborn.get_document(seeded_cv)
        assert view.edited_field_ids == [target.id]
        assert next(f for f in view.fields if f.id == target.id).text == "Persistée."

    def test_unknown_document_is_none(self, edit_service):
        assert edit_service.get_document("00000000deadbeef") is None

    def test_malformed_doc_id_is_refused(self, edit_service):
        """Un id d'URL sert à construire un chemin : il ne doit pas pouvoir en sortir."""
        from jobtool.services.document_edit_service import DocumentEditError

        with pytest.raises(DocumentEditError):
            edit_service.pdf_path("../../data")
