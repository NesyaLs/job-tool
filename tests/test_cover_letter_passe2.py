"""Preuve SANS clé API de la PASSE 2 de la lettre (rédaction + vérifs + boucle).

Stub du client (patch `call_structured` + `make_client` dans passe2). Drafts fabriqués pour
prouver les vérifs (placeholders, longueurs, cohérence mode/inserts, anti-chiffre) et la
convergence. ids de tests/fixtures/profil_demo.yaml.
"""

from __future__ import annotations

from unittest.mock import patch

from jobtool.documents.cover_letter import passe2
from jobtool.documents.cover_letter.schema import (
    CoverLetterDraftOutput,
    CoverLetterRoutingOutput,
)

ROUTING_GENERIQUE = CoverLetterRoutingOutput(
    entreprise="Acme Corp",
    domaine="data engineering",
    mode="generique",
    projets_ids=[],
    interets_ids=[],
)
ROUTING_CIBLE = CoverLetterRoutingOutput(
    entreprise="HPC Systems",
    domaine="calcul haute performance",
    mode="cible",
    projets_ids=["projet-noyaux-hpc.1", "projet-noyaux-hpc.2", "projet-noyaux-hpc.3"],
    interets_ids=["Contributions à des logiciels open source"],
)

# Briques de texte VALIDES, sobres et sous les plafonds.
VOUS_OK = "Le poste en calcul haute performance chez HPC Systems correspond à ce sur quoi je veux travailler après mes études."
MOI_OK = "Diplômé ingénieur en informatique, j'ai surtout travaillé sur des problèmes où la performance et la rigueur comptent."
NOUS_OK = "Je serais heureux d'en discuter lors d'un entretien."
FORMULE_OK = "Cordialement,"
PROJET_OK = (
    "J'ai écrit une version séquentielle de référence de deux noyaux de calcul, puis "
    "je les ai parallélisés avec OpenMP et MPI avant de les porter sur GPU CUDA."
)
INTERET_OK = "Je contribue à des projets open source, ce qui me garde proche des questions de performance au quotidien."


def draft(**overrides) -> CoverLetterDraftOutput:
    """Construit un draft VALIDE en mode ciblé, surchargé champ par champ par `overrides`."""
    base_fields = dict(
        paragraphe_vous=VOUS_OK,
        insert_interet=INTERET_OK,
        paragraphe_moi=MOI_OK,
        insert_projet=PROJET_OK,
        paragraphe_nous=NOUS_OK,
        formule=FORMULE_OK,
    )
    base_fields.update(overrides)
    return CoverLetterDraftOutput(**base_fields)


def run_case(base, routing, outputs, max_retries=2):
    """Exécute run_passe2 avec un client stubé renvoyant `outputs` (dernier répété au-delà)."""
    seq = list(outputs)
    counter = {"i": 0}

    def fake_call_structured(client, messages, response_format, **kwargs):
        out = seq[min(counter["i"], len(seq) - 1)]
        counter["i"] += 1
        return out

    attempts: list[tuple[int, list]] = []

    def on_attempt(attempt, parsed, issues):
        attempts.append((attempt, list(issues)))

    with (
        patch(
            "jobtool.core.llm.correction_loop.call_structured",
            side_effect=fake_call_structured,
        ),
        patch(
            "jobtool.documents.cover_letter.passe2.make_client",
            side_effect=lambda: object(),
        ),
    ):
        output, issues = passe2.run_passe2(
            base, routing, "OFFRE STUB", max_retries=max_retries, on_attempt=on_attempt
        )
    return output, issues, attempts


def test_generique_clean_inserts_none(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        ROUTING_GENERIQUE,
        [
            CoverLetterDraftOutput(
                paragraphe_vous="Votre poste de data engineer recoupe ce que je cherche.",
                insert_interet=None,
                paragraphe_moi=MOI_OK,
                insert_projet=None,
                paragraphe_nous=NOUS_OK,
                formule=FORMULE_OK,
            )
        ],
    )
    assert issues == [], "générique sans insert accepté (0 violation)"
    assert len(attempts) == 1, "accepté en 1 tentative"


def test_generique_with_forbidden_insert_then_converge(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        ROUTING_GENERIQUE,
        [
            CoverLetterDraftOutput(
                paragraphe_vous="Votre poste de data engineer recoupe ce que je cherche.",
                insert_interet=None,
                paragraphe_moi=MOI_OK,
                insert_projet="Un projet dont je ne devrais pas parler en générique.",
                paragraphe_nous=NOUS_OK,
                formule=FORMULE_OK,
            ),
            CoverLetterDraftOutput(
                paragraphe_vous="Votre poste de data engineer recoupe ce que je cherche.",
                insert_interet=None,
                paragraphe_moi=MOI_OK,
                insert_projet=None,
                paragraphe_nous=NOUS_OK,
                formule=FORMULE_OK,
            ),
        ],
    )
    assert any(v.kind == "unexpected_insert" for v in attempts[0][1]), (
        "tentative 1 : insert en générique => violation"
    )
    assert issues == [], "tentative 2 (insert retiré) : acceptée"


def test_cible_clean(moi_base):
    out, issues, attempts = run_case(moi_base, ROUTING_CIBLE, [draft()])
    assert issues == [], "ciblé propre accepté (0 violation)"
    assert len(attempts) == 1, "accepté en 1 tentative"


def test_invented_number_corrected(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        ROUTING_CIBLE,
        [
            draft(insert_projet=PROJET_OK + " Cela a donné une accélération de 12x."),
            draft(),
        ],
    )
    assert len(attempts) >= 2, "la boucle a redemandé après la violation"
    assert any(v.kind == "invented_number" for v in attempts[0][1]), (
        "tentative 1 : nombre absent des sources => anti-chiffre"
    )
    assert issues == [], "tentative 2 (sans chiffre) : acceptée"


def test_residual_placeholder_corrected(moi_base):
    out, issues, attempts = run_case(
        moi_base,
        ROUTING_CIBLE,
        [
            draft(
                paragraphe_vous="Le travail de [entreprise] en HPC m'intéresse depuis un moment."
            ),
            draft(),
        ],
    )
    assert len(attempts) >= 2, "la boucle a redemandé après la violation"
    assert any(v.kind == "residual_placeholder" for v in attempts[0][1]), (
        "tentative 1 : [entreprise] résiduel => violation (CRITIQUE)"
    )
    assert issues == [], "tentative 2 (substitué) : acceptée"


def test_too_long_paragraph_corrected(moi_base):
    trop_long = ("Je serais ravi d'échanger. " * 30).strip()  # >> MAX_NOUS_CHARS
    out, issues, attempts = run_case(
        moi_base,
        ROUTING_CIBLE,
        [draft(paragraphe_nous=trop_long), draft()],
    )
    assert len(attempts) >= 2, "la boucle a redemandé après la violation"
    assert any(v.kind == "too_long" for v in attempts[0][1]), (
        "tentative 1 : paragraphe_nous trop long => « trop long »"
    )
    assert issues == [], "tentative 2 (raccourci) : acceptée"


# --- Lettre type du profil ----------------------------------------------------


def _with_example(base, text):
    documents = base.documents.model_copy(update={"letter_example": text})
    return base.model_copy(update={"documents": documents})


def test_lettre_type_sert_de_base(moi_base, tmp_path):
    """Avec une lettre type, elle part au modèle comme BASE (règles dédiées), à la place
    de l'ancre de ton."""
    (tmp_path / "exemple_generique.txt").write_text("ANCRE DE TON", encoding="utf-8")
    example = "Madame, Monsieur,\n\nJe vous écris pour le poste chez Ancienne Boîte."
    base = _with_example(moi_base, example)

    system = passe2.system_prompt(base)
    context = passe2._build_context(base, ROUTING_GENERIQUE, "Offre", tmp_path)

    assert "LETTRE TYPE DE LA PERSONNE" in system and "PRIME" in system
    assert example in context and "base à adapter" in context
    assert "ANCRE DE TON" not in context, "la lettre type remplace l'ancre de ton"


def test_sans_lettre_type_rien_ne_change(moi_base, tmp_path):
    (tmp_path / "exemple_generique.txt").write_text("ANCRE DE TON", encoding="utf-8")
    base = _with_example(moi_base, None)

    assert passe2.system_prompt(base) == passe2._SYSTEM_PROMPT
    context = passe2._build_context(base, ROUTING_GENERIQUE, "Offre", tmp_path)
    assert "ANCRE DE TON" in context and "LETTRE TYPE" not in context
