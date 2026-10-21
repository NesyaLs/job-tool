"""Adaptateur de génération pour l'API : enrobe les services de génération existants.

L'API a besoin d'une issue UNIFORME et SANS exception : soit un chemin de PDF (succès), soit
un message d'erreur lisible (échec) — jamais un faux succès. On délègue à
`services.batch_service` (process_generate / process_letter), qui ne lèvent jamais et
capturent déjà les erreurs d'étape. AUCUNE logique métier ici : on prépare les entrées
(base d'expériences, profil de template, chemin de sortie) et on relaie le résultat.

Imports LOURDS différés (la chaîne LLM via mistralai) DANS les méthodes : importer ce module
— et donc l'app FastAPI — ne doit nécessiter ni clé ni SDK (parité avec les commandes CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jobtool.aggregator.models import Job
from jobtool.core.experience_base.store import ExperienceBaseRepo
from jobtool.services.usage_service import UsageRecorder, noop_recorder


@dataclass(frozen=True)
class GenerationOutcome:
    """Issue uniforme d'une génération : `pdf_path` (succès) XOR `error` (échec)."""

    pdf_path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.pdf_path is not None


class Generator:
    """Produit CV/lettre pour une offre, par-dessus les services de génération.

    Reçoit le DÉPÔT de base d'expériences du compte appelant (et non un chemin de fichier :
    ce serait le même fichier pour tous les comptes) et un dossier de sortie ; reste léger à
    la construction. Les PDF sont écrits sous `output/api/` (le binaire Typst ne peut pas
    écrire dans /tmp) ; le nom inclut l'id de l'offre pour éviter les collisions.
    """

    def __init__(
        self,
        base_repo: ExperienceBaseRepo,
        output_dir: str | Path = "output/api",
        record_usage: UsageRecorder = noop_recorder,
        api_key: str | None = None,
    ) -> None:
        self._base_repo = base_repo
        self._output_dir = Path(output_dir)
        self._record_usage = record_usage
        self._api_key = api_key  # clé Mistral du compte (None : celle du serveur)

    def _note_call(self, kind: str) -> None:
        """Enregistre la consommation. Import différé : `MODEL` vit dans la chaîne LLM."""
        from jobtool.core.llm.client import MODEL

        self._record_usage(kind, MODEL)

    def _prepare_out(self, prefix: str, offer: Job) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        return self._output_dir / f"{prefix}-{offer.dedup_hash}.pdf"

    def generate_cv(self, offer: Job, template: str = "classic") -> GenerationOutcome:
        """Génère + rend + contrôle un CV pour l'offre. Renvoie un PDF ou une erreur lisible."""
        from jobtool.core.llm.client import using_api_key
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.services.batch_service import process_generate

        try:
            base = self._base_repo.load()
            profile = get_profile(template)
        except Exception as exc:  # noqa: BLE001 — base/profil indisponible : échec structuré
            return GenerationOutcome(error=f"préparation : {type(exc).__name__}: {exc}")

        out_pdf = self._prepare_out("cv", offer)
        # Noté AVANT de savoir si la génération aboutit : un appel raté au modèle est
        # facturé comme un autre, et c'est ce qu'on cherche à compter.
        self._note_call("cv")
        with using_api_key(self._api_key):
            result = process_generate(base, profile, offer.description, out_pdf, template)
        if result.rendered and result.pdf_path:
            return GenerationOutcome(pdf_path=result.pdf_path)
        return GenerationOutcome(error=result.error or "génération du CV échouée")

    def generate_letter(self, offer: Job) -> GenerationOutcome:
        """Génère + rend + contrôle une lettre pour l'offre. PDF ou erreur lisible."""
        from jobtool.core.llm.client import using_api_key
        from jobtool.services.batch_service import process_letter

        try:
            base = self._base_repo.load()
        except Exception as exc:  # noqa: BLE001
            return GenerationOutcome(error=f"préparation : {type(exc).__name__}: {exc}")

        out_pdf = self._prepare_out("lettre", offer)
        self._note_call("letter")
        with using_api_key(self._api_key):
            result = process_letter(base, offer.description, out_pdf)
        if result.rendered and result.pdf_path:
            return GenerationOutcome(pdf_path=result.pdf_path)
        return GenerationOutcome(
            error=result.error or "génération de la lettre échouée"
        )
