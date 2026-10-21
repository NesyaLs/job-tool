"""Routes /documents : retouche manuelle d'un CV/lettre déjà généré.

Même contrat que les autres routers : recevoir -> appeler un service -> renvoyer du JSON
(ou un fichier). Aucune logique métier, aucun SQL, aucun accès domaine en direct — tout
passe par `DocumentEditService` (injecté).

Les chemins de fichiers du serveur ne sortent JAMAIS dans les réponses : le service rend
des chemins disque, le router les projette en URL (`/documents/{id}/pdf`,
`/documents/{id}/preview/{n}.png`) servies par ces mêmes routes.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from jobtool.api.deps import (
    get_current_user_id,
    get_document_edit_service,
    get_document_jobs,
    get_offers_service,
    get_profile_key,
)
from jobtool.api.schemas import (
    CvTemplateOut,
    CvTemplatesOut,
    DocumentChecksOut,
    DocumentJobOut,
    DocumentJobRequest,
    DocumentJobStepOut,
    DocumentOpenRequest,
    DocumentSourceEdit,
    DocumentSourceOut,
    DocumentTemplateEdit,
    DocumentView,
    EditableFieldOut,
    FieldEdits,
    FieldReset,
    OfferDocumentOut,
)
from jobtool.services.document_edit_service import (
    DocumentEditError,
    DocumentEditService,
    DocumentSourceError,
    DocumentTemplateError,
    SourceModeActive,
)
from jobtool.services.document_edit_service import DocumentView as ServiceView
from jobtool.services.document_jobs import DocumentJob, DocumentJobs
from jobtool.services.offers_service import OffersService
from jobtool.services.template_service import (
    DEFAULT_CV_TEMPLATE,
    DEFAULT_LETTER_TEMPLATE,
    letter_template_previews,
    offered_cv_templates,
    offered_letter_templates,
    template_previews,
)

router = APIRouter(prefix="/documents", tags=["documents"])

_KINDS = ("cv", "letter")


def _to_view(view: ServiceView) -> DocumentView:
    """Projette la vue du service vers le contrat HTTP : chemins disque -> URL.

    C'est la SEULE chose que cette fonction apporte, et la raison d'être du router : les
    chemins du serveur ne sortent jamais dans une réponse. Le reste des champs porte les
    mêmes noms de part et d'autre — les recopier un à un n'ajoutait aucune information, et
    faisait qu'un champ ajouté au service devait être pensé ici aussi pour ne pas
    disparaître silencieusement de la réponse.

    `checks.ok` est une propriété calculée du service : `asdict` ne la voit pas, on la
    passe explicitement.
    """
    return DocumentView(
        source_edited=view.source_edited,
        doc_id=view.doc_id,
        offer_id=view.offer_id,
        kind=view.kind,
        template=view.template,
        options=view.options,
        page_count=view.page_count,
        fields=[EditableFieldOut(**asdict(f)) for f in view.fields],
        checks=DocumentChecksOut(ok=view.checks.ok, **asdict(view.checks)),
        edited_field_ids=view.edited_field_ids,
        pdf_url=f"/documents/{view.doc_id}/pdf",
        preview_urls=[
            f"/documents/{view.doc_id}/preview/{page}.png"
            for page in range(1, len(view.preview_paths) + 1)
        ],
    )


def _view_or_404(view: ServiceView | None, doc_id: str) -> DocumentView:
    if view is None:
        raise HTTPException(status_code=404, detail=f"document introuvable : {doc_id}")
    return _to_view(view)


@router.get("/templates", response_model=CvTemplatesOut)
def list_templates(kind: str = "cv") -> CvTemplatesOut:
    """Mises en page proposées au choix avant une génération (`kind` : cv | letter).

    Même forme pour les deux types : le front construit le même sélecteur. Déclarée AVANT
    `/{doc_id}` : sinon « templates » serait pris pour un identifiant.
    """
    if kind not in _KINDS:
        raise HTTPException(
            status_code=422, detail=f"type de document inconnu : {kind}"
        )
    if kind == "letter":
        return CvTemplatesOut(
            templates=[
                CvTemplateOut(
                    name=spec.name,
                    label=spec.label,
                    description=spec.description,
                    options=[],
                    previews=letter_template_previews(spec),  # type: ignore[arg-type]
                )
                for spec in offered_letter_templates()
            ],
            default=DEFAULT_LETTER_TEMPLATE,
        )
    return CvTemplatesOut(
        templates=[
            CvTemplateOut.model_validate(
                {**spec.model_dump(), "previews": template_previews(spec)}
            )
            for spec in offered_cv_templates()
        ],
        default=DEFAULT_CV_TEMPLATE,
    )


# --- Génération en arrière-plan ------------------------------------------------
#
# Déclarées AVANT `/{doc_id}` : sinon « jobs » et « offer » seraient pris pour des ids.


def _job_out(job: DocumentJob) -> DocumentJobOut:
    return DocumentJobOut(
        id=job.id,
        offer_id=job.offer_id,
        offer_title=job.offer_title,
        status=job.status,
        steps=[DocumentJobStepOut(**asdict(step)) for step in job.steps],
        started_at=job.started_at.isoformat(),
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.post("/jobs", response_model=DocumentJobOut, status_code=202)
def start_job(
    body: DocumentJobRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_key: str = Depends(get_profile_key),
    offers: OffersService = Depends(get_offers_service),
    service: DocumentEditService = Depends(get_document_edit_service),
    jobs: DocumentJobs = Depends(get_document_jobs),
) -> DocumentJobOut:
    """Lance la génération du CV PUIS de la lettre de l'offre, et rend la main aussitôt.

    Templates : ceux choisis par défaut dans le profil. Un document déjà préparé pour
    l'offre est rouvert tel quel, sauf `force_new`. On suit la tâche par GET /documents/jobs.
    """
    offer = offers.get_offer(body.offer_id)
    if offer is None:
        raise HTTPException(
            status_code=404, detail=f"offre introuvable : {body.offer_id}"
        )
    cv_template, letter_template = service.default_templates()
    templates = {"cv": cv_template, "letter": letter_template}

    def make(kind: str) -> str:
        view = service.open_document(
            offer_id=offer.dedup_hash,
            offer_description=offer.description,
            kind=kind,  # type: ignore[arg-type]
            template=templates[kind],
            force_new=body.force_new,
        )
        return view.doc_id

    job = jobs.start((user_id, profile_key), offer.dedup_hash, offer.title, make)
    return _job_out(job)


@router.get("/jobs", response_model=list[DocumentJobOut])
def list_jobs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_key: str = Depends(get_profile_key),
    jobs: DocumentJobs = Depends(get_document_jobs),
) -> list[DocumentJobOut]:
    """Générations en cours et récentes du compte (profil actif), les plus récentes d'abord."""
    return [_job_out(job) for job in jobs.list((user_id, profile_key))]


@router.get("/offer/{offer_id}", response_model=list[OfferDocumentOut])
def offer_documents(
    offer_id: str,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> list[OfferDocumentOut]:
    """Le CV et la lettre déjà prêts pour l'offre (liste vide s'il n'y en a pas)."""
    try:
        found = service.offer_documents(offer_id)
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        OfferDocumentOut(
            **asdict(doc),
            pdf_url=f"/documents/{doc.doc_id}/pdf",
            preview_url=f"/documents/{doc.doc_id}/preview/1.png",
        )
        for doc in found
    ]


@router.post("", response_model=DocumentView)
def open_document(
    body: DocumentOpenRequest,
    offers: OffersService = Depends(get_offers_service),
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Ouvre l'écran d'édition : reprend le brouillon de l'offre, ou en génère un (bloquant).

    502 + raison lisible si la génération ou le rendu échoue — jamais un faux 200.
    """
    if body.kind not in _KINDS:
        raise HTTPException(
            status_code=422, detail=f"type de document inconnu : {body.kind}"
        )

    offer = offers.get_offer(body.offer_id)
    if offer is None:
        raise HTTPException(
            status_code=404, detail=f"offre introuvable : {body.offer_id}"
        )

    try:
        view = service.open_document(
            offer_id=offer.dedup_hash,
            offer_description=offer.description,
            kind=body.kind,  # type: ignore[arg-type]
            template=body.template_ref,
            force_new=body.force_new,
        )
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _to_view(view)


@router.get("/{doc_id}", response_model=DocumentView)
def get_document(
    doc_id: str,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Recompile et renvoie l'état courant d'un document retouchable. 404 si inconnu."""
    try:
        return _view_or_404(service.get_document(doc_id), doc_id)
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.patch("/{doc_id}/fields", response_model=DocumentView)
def edit_fields(
    doc_id: str,
    body: FieldEdits,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Applique des retouches, RECOMPILE, et renvoie l'état + le verdict des contrôles.

    Le rendu et les contrôles sont refaits ici, pas plus tard : le front ne peut pas
    afficher un document « valide » qui n'aurait pas été remesuré après l'édition.
    """
    try:
        return _view_or_404(service.set_fields(doc_id, body.edits), doc_id)
    except SourceModeActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{doc_id}/reset", response_model=DocumentView)
def reset_fields(
    doc_id: str,
    body: FieldReset | None = None,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Rend leur texte GÉNÉRÉ aux champs désignés (corps absent/vide = tout remettre)."""
    field_ids = body.field_ids if body is not None else []
    try:
        return _view_or_404(service.reset_fields(doc_id, field_ids), doc_id)
    except SourceModeActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/{doc_id}/template", response_model=DocumentView)
def set_template(
    doc_id: str,
    body: DocumentTemplateEdit,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Recompile le document dans une autre mise en page, SANS rappeler le modèle.

    Texte, retouches de zones et source du mode pro sont conservés. Lettre seulement :
    422 pour un nom inconnu ou un CV (qui se régénère pour changer de template).
    """
    try:
        return _view_or_404(service.set_template(doc_id, body.template), doc_id)
    except DocumentTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Mode pro : la source de rendu ------------------------------------------


@router.get("/{doc_id}/source", response_model=DocumentSourceOut)
def get_source(
    doc_id: str,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentSourceOut:
    """Données du document telles qu'elles partent au compilateur (JSON indenté)."""
    found = service.get_source(doc_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"document introuvable : {doc_id}")
    source, edited = found
    return DocumentSourceOut(source=source, source_edited=edited)


@router.put("/{doc_id}/source", response_model=DocumentView)
def set_source(
    doc_id: str,
    body: DocumentSourceEdit,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Remplace les données du document, RECOMPILE et RECONTRÔLE ; renvoie la vue.

    Deux échecs, deux codes : une source mal écrite ou qui ne compile pas est une erreur
    de la requête (422, rien n'est enregistré) ; un échec derrière une source valide est
    une erreur du serveur (502).
    """
    try:
        return _view_or_404(service.set_source(doc_id, body.source), doc_id)
    except DocumentSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{doc_id}/source/reset", response_model=DocumentView)
def reset_source(
    doc_id: str,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> DocumentView:
    """Quitte le mode pro : données générées et retouches de zones, intactes."""
    try:
        return _view_or_404(service.reset_source(doc_id), doc_id)
    except DocumentEditError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Artefacts (aperçu, PDF) ------------------------------------------------


def _artifact_or_404(resolve, what: str) -> FileResponse:
    """Sert un artefact du dossier de travail, ou 404 — y compris sur un id malformé.

    Un id d'URL qui ne ressemble pas à un id émis est refusé par le service AVANT de
    toucher au disque ; on le traite comme une absence, pas comme une erreur serveur.
    """
    try:
        path: Path = resolve()
    except DocumentEditError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{what} introuvable")
    media = "application/pdf" if path.suffix == ".pdf" else "image/png"
    return FileResponse(str(path), media_type=media)


@router.get("/{doc_id}/preview/{page}.png", response_model=None)
def get_preview(
    doc_id: str,
    page: int,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> FileResponse:
    """Aperçu PNG d'une page, produit par le MÊME rendu Typst que le PDF.

    C'est l'image sur laquelle le front superpose les zones éditables : aperçu et
    coordonnées viennent de la même compilation, ils ne peuvent pas se désaccorder.
    """
    return _artifact_or_404(
        lambda: service.preview_path(doc_id, page), f"aperçu page {page} de {doc_id}"
    )


@router.get("/{doc_id}/pdf", response_model=None)
def get_pdf(
    doc_id: str,
    service: DocumentEditService = Depends(get_document_edit_service),
) -> FileResponse:
    """PDF courant du document (retouches comprises), tel qu'il sera téléchargé."""
    return _artifact_or_404(lambda: service.pdf_path(doc_id), f"PDF de {doc_id}")
