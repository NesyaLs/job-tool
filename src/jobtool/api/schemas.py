"""Schémas d'entrée/sortie de l'API (Pydantic) — pur transport, aucune logique métier.

Ces modèles ne sont QUE le contrat HTTP : ils projettent le modèle interne `Job` vers les
champs dont le front a besoin (et inversement pour les corps de requête). Aucune règle métier
ici : la projection est un simple mapping de présentation, faite dans `from_job`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from jobtool.aggregator.matching import MatchResult
from jobtool.aggregator.models import Job, JobStatus
from jobtool.services.template_service import (
    LetterTemplateError,
    OptionValue,
    TemplateOptionError,
    canonical_cv_template,
    canonical_letter_template,
)


class OfferLink(BaseModel):
    """Un lien de l'offre sur UNE source. Une offre dédoublonnée en a plusieurs."""

    source: str
    url: str


class OfferSummary(BaseModel):
    """Offre telle que listée par le front (champs strictement nécessaires)."""

    id: str  # == dedup_hash (identité canonique)
    titre: str
    entreprise: str
    lieu: str
    source: str
    date: str | None  # date de publication (posted_date), si connue
    statut: JobStatus
    favorite: bool
    description: str
    # Lien vers l'annonce source : dans le RÉSUMÉ (pas seulement le détail) pour que la
    # liste affiche le lien sans un GET détail par offre dépliée. Peut être vide (le
    # front n'affiche alors aucun lien) : toutes les sources ne fournissent pas d'URL.
    url: str
    # Date de candidature (ISO) si l'offre est marquée postulée — alimente l'écran
    # « Candidatures ». None : pas postulée, ou date inconnue (offre antérieure à la colonne).
    applied_at: str | None
    # Date du masquage (ISO) si l'offre est masquée (statut `seen`) — écran « Masquées ».
    hidden_at: str | None = None
    # --- Champs NORMALISÉS (calculés par le code, jamais saisis) ---------------------
    # `lieu` et `contract_type` (détail) restent le texte de la source ; ceux-ci servent à
    # filtrer et à afficher de façon homogène d'une source à l'autre.
    contract_kind: str = "inconnu"  # cdi|cdd|stage|alternance|interim|freelance|inconnu
    work_time: str | None = None  # temps_plein | temps_partiel
    country: str | None = None  # ISO2
    region: str | None = None
    department: str | None = None  # code INSEE
    city: str | None = None
    # commune | commune_homonyme | departement | region | pays | inconnu
    place_precision: str = "inconnu"
    # Tous les liens de l'offre, par source, le principal d'abord (= `url`, `source`).
    sources: list[OfferLink] = Field(default_factory=list)
    # Plus revue par aucune collecte, conservée parce que ce compte y tient.
    expired: bool = False
    private: bool = False  # saisie à la main, visible de ce seul compte

    @classmethod
    def from_job(cls, job: Job) -> OfferSummary:
        return cls(
            contract_kind=job.contract_kind,
            work_time=job.work_time,
            country=job.country,
            region=job.region,
            department=job.department,
            city=job.city,
            place_precision=job.place_precision,
            sources=[OfferLink(**link) for link in job.links],
            expired=job.expired_at is not None,
            private=job.private,
            id=job.dedup_hash,
            titre=job.title,
            entreprise=job.company,
            lieu=job.location,
            source=job.source,
            date=job.posted_date,
            statut=job.status,
            favorite=job.favorite,
            description=job.description,
            url=job.url,
            applied_at=job.applied_at,
            hidden_at=job.hidden_at,
        )


class OfferDetail(OfferSummary):
    """Offre complète (détail) : ajoute les champs secondaires au résumé."""

    native_id: str
    remote: str
    contract_type: str
    fetched_date: str

    @classmethod
    def from_job(cls, job: Job) -> OfferDetail:
        """Le résumé, plus les champs du détail.

        Recopié champ par champ, ce constructeur redisait les dix projections du parent :
        un champ ajouté au résumé était projeté ici ou pas, selon qu'on avait pensé aux
        deux endroits.
        """
        return cls(
            **OfferSummary.from_job(job).model_dump(),
            native_id=job.native_id,
            remote=job.remote,
            contract_type=job.contract_type,
            fetched_date=job.fetched_date,
        )


class MatchCriterionOut(BaseModel):
    """Contribution d'un critère au score d'une carte (détail affiché sous la jauge)."""

    key: str
    label: str
    points: float  # points obtenus (0 – max_points)
    max_points: float  # part du critère dans les 100 points
    detail: str


class StackCard(OfferDetail):
    """Une carte de la pile « Découvrir » : l'offre, son score et le détail du score.

    Le score est CALCULÉ côté serveur (aggregator.matching) : le front ne fait que
    l'afficher. Arrondis faits ici à 0,1 point, pour que le détail reste lisible.
    """

    score: int
    criteres: list[MatchCriterionOut]

    @classmethod
    def from_match(cls, match: MatchResult) -> StackCard:
        return cls(
            **OfferDetail.from_job(match.job).model_dump(),
            score=match.percent,
            criteres=[
                MatchCriterionOut(
                    key=c.key,
                    label=c.label,
                    points=round(c.points, 1),
                    max_points=round(c.weight, 1),
                    detail=c.detail,
                )
                for c in match.criteria
            ],
        )


# --- Corps de requête (PATCH) -----------------------------------------------


class StatusUpdate(BaseModel):
    """Corps de PATCH /offers/{id}/status."""

    status: JobStatus


class FavoriteUpdate(BaseModel):
    """Corps de PATCH /offers/{id}/favorite.

    `favorite` optionnel : absent -> on BASCULE l'état courant ; fourni -> on FORCE la valeur.
    """

    favorite: bool | None = None


class DescriptionUpdate(BaseModel):
    """Corps de PATCH /offers/{id}/description."""

    description: str


# --- Ajout manuel d'une offre (POST /offers) --------------------------------


class ManualOfferCreate(BaseModel):
    """Corps de POST /offers : une offre que l'agrégateur n'a pas récupérée.

    `titre` et `url` sont OBLIGATOIRES — sans lien, l'offre serait irrécupérable. Les autres
    champs sont facultatifs et peuvent rester vides (l'utilisateur complétera plus tard depuis
    la page « postuler »). La validation reste de la FORME (présence, URL exploitable) : la
    règle métier de déduplication vit dans le service.
    """

    titre: str = Field(min_length=1)
    url: str = Field(min_length=1)
    description: str = ""
    entreprise: str = ""
    lieu: str = ""

    @field_validator("titre", "url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Refuse une suite d'espaces (min_length seul l'accepterait)."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("champ obligatoire")
        return cleaned

    @field_validator("url")
    @classmethod
    def _usable_url(cls, value: str) -> str:
        """Refuse une URL dont on ne peut pas dériver d'identité (ni schéma ni hôte)."""
        from jobtool.aggregator.normalize import normalize_url

        if not normalize_url(value):
            raise ValueError("lien invalide : attendu une URL http(s) complète")
        return value


# --- Erreur de génération (jamais un faux 200) ------------------------------


class GenerationError(BaseModel):
    """Corps JSON renvoyé quand une génération CV/lettre échoue."""

    offer_id: str
    kind: str  # "cv" ou "letter"
    error: str  # raison lisible de l'échec (jamais de secret)


# --- Retouche manuelle d'un document généré ---------------------------------


class EditableFieldOut(BaseModel):
    """Une zone retouchable + sa position sur l'aperçu, en POURCENTAGES de la page.

    Des pourcentages (et non des pixels) : l'aperçu peut être affiché à n'importe quelle
    taille, le front pose les zones sans connaître ni la résolution ni l'échelle.
    """

    id: str  # pour une puce de CV : son id de traçabilité
    text: str  # texte actuellement rendu
    original: str  # texte issu de la génération (cible du « revenir au texte généré »)
    edited: bool  # vrai si ce texte a été réécrit à la main
    page: int
    left: float
    top: float
    width: float
    height: float


class DocumentChecksOut(BaseModel):
    """Verdict des bornes dures, mesuré sur le document TEL QU'IL EST RENDU."""

    ok: bool
    pages: int
    page_violations: list[str]  # dont « le CV fait 2 pages, attendu 1 »
    missing_ats: list[str]  # texte non ré-extractible du PDF
    overruns: list[str]  # budget du template (longueurs, comptages)
    violations: list[str]  # traçabilité (ids inconnus)
    layout_warnings: list[str]  # mise en page mesurée par Typst (titre, coordonnées)


class DocumentView(BaseModel):
    """État complet de l'écran d'édition après un rendu."""

    doc_id: str
    offer_id: str
    kind: str  # "cv" | "letter"
    template: str  # template de BASE (« basic », « sobre »…)
    options: dict[str, OptionValue]  # options résolues (défauts compris)
    page_count: int
    fields: list[EditableFieldOut]
    checks: DocumentChecksOut
    edited_field_ids: list[str]
    pdf_url: str
    preview_urls: list[str]  # un aperçu PNG par page, dans l'ordre
    # Mode pro actif : source réécrite en entier ; zones inactives, contenu non jugé.
    source_edited: bool


class DocumentOpenRequest(BaseModel):
    """Corps de POST /documents : ouvrir (ou régénérer) le document d'une offre."""

    offer_id: str
    kind: str = "cv"  # "cv" | "letter"
    # Template du CV. ABSENT : on reprend le dernier brouillon de l'offre quel qu'il soit
    # (et, faute de brouillon, on génère avec le template par défaut). FOURNI : on ne
    # reprend qu'un brouillon de CE template, sinon on génère — le choix est respecté.
    # Lettre : on reprend toujours son dernier brouillon ; un template FOURNI et différent
    # le recompile dans cette mise en page (sans rappeler le modèle).
    template: str | None = None
    # Options du template (déclarées côté serveur, cf. GET /documents/templates). Les
    # options absentes prennent leur valeur par défaut. Exigent un `template`.
    options: dict[str, OptionValue] = Field(default_factory=dict)
    # Par défaut on REPREND le brouillon existant : régénérer coûte un appel LLM et
    # écraserait les retouches en cours. Le forcer est donc un choix explicite.
    force_new: bool = False

    _template_ref: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _template_is_valid(self) -> DocumentOpenRequest:
        """Template ET options validés ICI (422), AVANT tout appel au modèle.

        Le choix est ramené à sa référence canonique (`nom?options`), qui sert de clé de
        reprise du brouillon : deux écritures du même choix désignent le même document.
        """
        if self.kind == "letter":
            if self.options:
                raise ValueError("une lettre n'a pas d'options de template")
            if self.template is not None:
                try:
                    self._template_ref = canonical_letter_template(self.template)
                except LetterTemplateError as exc:
                    raise ValueError(str(exc)) from exc
            return self
        if self.kind != "cv":
            return self
        if self.template is None:
            if self.options:
                raise ValueError("options fournies sans template")
            return self
        try:
            self._template_ref = canonical_cv_template(self.template, self.options)
        except TemplateOptionError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @property
    def template_ref(self) -> str | None:
        """Référence canonique du choix (None : aucun template demandé)."""
        return self._template_ref


class OptionChoiceOut(BaseModel):
    value: str
    label: str


class TemplateOptionOut(BaseModel):
    """Une option réglable, telle que le front construit son contrôle."""

    name: str
    label: str
    kind: str  # "bool" (case à cocher) | "choice" (liste de valeurs)
    default: OptionValue
    choices: list[OptionChoiceOut]
    affects_budget: bool  # True : change la place disponible sur la page


class TemplatePreviewOut(BaseModel):
    """Une miniature pré-générée, pour une combinaison d'options VISUELLES."""

    options: dict[str, OptionValue]  # options visuelles représentées ({} : aucune)
    url: str  # relative à la racine du front


class CvTemplateOut(BaseModel):
    """Un template proposé (CV ou lettre : même forme, une lettre n'a pas d'options)."""

    name: str
    label: str
    description: str
    options: list[TemplateOptionOut]
    previews: list[TemplatePreviewOut]
    # True : mise en page récente, annoncée « bêta » dans l'interface. Défaut False, donc
    # la lettre (dont les specs n'ont pas ce champ) n'en porte jamais.
    beta: bool = False


class CvTemplatesOut(BaseModel):
    """Réponse de GET /documents/templates[?kind=] : mises en page proposées au choix."""

    templates: list[CvTemplateOut]  # dans l'ordre d'affichage
    default: str


class DocumentSourceOut(BaseModel):
    """Réponse de GET /documents/{id}/source : l'entrée de compilation, éditable."""

    source: str  # JSON indenté : exactement ce qui a produit l'aperçu courant
    source_edited: bool  # True : mode pro actif (la source a déjà été réécrite)


class DocumentSourceEdit(BaseModel):
    """Corps de PUT /documents/{id}/source : la source réécrite, en TEXTE.

    Du texte et non un objet : c'est ce que l'utilisateur a tapé, et il doit pouvoir être
    invalide — le service le refuse avec une raison lisible (ligne, colonne, champ).
    """

    source: str


class DocumentTemplateEdit(BaseModel):
    """Corps de PUT /documents/{id}/template : le nom du template à appliquer.

    Validé par le service (qui connaît le type du document) : 422 si inconnu.
    """

    template: str


class FieldEdits(BaseModel):
    """Corps de PATCH /documents/{id}/fields : {id de champ -> nouveau texte}."""

    edits: dict[str, str]


class FieldReset(BaseModel):
    """Corps de POST /documents/{id}/reset. Liste vide = tout réinitialiser."""

    field_ids: list[str] = []


class AuthConfigOut(BaseModel):
    """Ce que le navigateur a besoin de savoir pour se connecter. Aucun secret ici."""

    auth_required: bool
    supabase_url: str
    publishable_key: str


class ApiKeyStatus(BaseModel):
    """État d'UNE clé d'API pour le compte. Jamais la valeur : seulement d'où elle vient."""

    name: str
    label: str
    account: bool  # le compte a saisi sa propre clé
    server: bool  # le serveur en fournit une (utilisée si le compte n'en a pas)


class ApiKeysOut(BaseModel):
    """Clés d'API du compte. `can_store` : faux si le serveur ne peut pas les chiffrer."""

    can_store: bool
    keys: list[ApiKeyStatus]


class ApiKeyIn(BaseModel):
    """Valeur d'une clé à enregistrer (jamais renvoyée ni journalisée)."""

    value: str = Field(min_length=1, max_length=512)


class MeOut(BaseModel):
    """Ce que le front doit savoir du compte connecté pour composer ses menus.

    `is_admin` n'ouvre rien par lui-même : il sert à afficher les entrées Administration et
    Journal LLM. Les routes /admin/* gardent leur propre contrôle (403).
    """

    is_admin: bool


# --- Administration (compteurs, jamais de contenu client) -------------------


class AdminAccountOut(BaseModel):
    """Un compte et ses volumes, tels qu'affichés par l'écran d'administration."""

    id: str
    email: str
    display_name: str
    is_admin: bool
    created_at: str
    offers: int
    favorites: int
    applied: int
    documents: int
    llm_calls: int


class JournalRunOut(BaseModel):
    """Une génération dans la LISTE du journal : de quoi la situer, aucun prompt."""

    run_id: str
    kind: str  # cv | letter
    template: str
    model: str
    status: str  # running | ok | error
    error: str | None
    calls: int
    started_at: str
    ended_at: str | None
    account: str  # e-mail du compte pour lequel on a généré
    offer_id: str | None
    offer_title: str  # "" quand l'offre n'est pas (ou plus) au catalogue


class JournalExchangeOut(BaseModel):
    """UN appel au modèle, intégral : c'est l'objet même du journal."""

    seq: int
    step: str  # routage | redaction | cv_a | cv_b
    attempt: int
    retries_429: int
    messages: list[Any]  # ce qui est PARTI au modèle, tel quel
    response: Any | None  # la structure renvoyée, ou null


class JournalRunDetailOut(BaseModel):
    """Réponse de GET /admin/llm-runs/{id} : la génération et ses appels, dans l'ordre."""

    run: JournalRunOut
    exchanges: list[JournalExchangeOut]


class AdminOverviewOut(BaseModel):
    """Vue d'ensemble de l'instance."""

    accounts: list[AdminAccountOut]
    catalogue_offers: int
    total_documents: int
    total_llm_calls: int
    database: str


# --- Collecte (déclenchement et suivi) ---------------------------------------


class SourceProgress(BaseModel):
    """Avancement d'UNE source pendant une collecte, puis son bilan.

    `status` : pending | running | done | failed | skipped (écartée faute d'identifiants).
    """

    source: str
    status: str
    searches_done: int = 0
    searches_total: int = 0
    fetched: int = 0
    errors: list[str] = Field(default_factory=list)


class CollectionSummary(BaseModel):
    """Bilan d'une collecte terminée."""

    fetched: int = 0  # offres récupérées, toutes sources confondues
    distinct: int = 0  # offres canoniques touchées (après dédoublonnage)
    new_offers: int = 0  # offres CRÉÉES au catalogue
    already_known: int = 0
    # Rattachements par niveau : identifiant, url, approchee, nouvelle.
    by_level: dict[str, int] = Field(default_factory=dict)
    purged: int = 0  # offres expirées retirées
    kept_expired: int = 0  # offres expirées gardées (un compte y tient)


_PROGRESS_FIELDS = ("status", "searches_done", "searches_total", "fetched", "errors")


class CollectionStatus(BaseModel):
    """Une collecte : réponse de POST /aggregate (202) et de GET /aggregate/{id}.

    `status` : running | done | failed. `error_count` compte les ERREURS (une source peut
    en avoir plusieurs, et fonctionner quand même).
    """

    id: str
    status: str
    trigger: str  # api | cli
    started_at: str
    finished_at: str | None = None
    sources: list[SourceProgress] = Field(default_factory=list)
    summary: CollectionSummary | None = None
    errors: list[str] = Field(default_factory=list)  # hors sources
    error_count: int = 0

    @classmethod
    def from_view(cls, view: Any) -> CollectionStatus:
        sources = [
            SourceProgress(
                source=name,
                **{k: v for k, v in data.items() if k in _PROGRESS_FIELDS},
            )
            for name, data in sorted(view.sources.items())
        ]
        return cls(
            id=view.id,
            status=view.status,
            trigger=view.trigger,
            started_at=view.started_at,
            finished_at=view.finished_at,
            sources=sources,
            summary=CollectionSummary(**view.summary) if view.summary else None,
            errors=view.errors,
            error_count=len(view.errors) + sum(len(src.errors) for src in sources),
        )


# --- Génération en arrière-plan ------------------------------------------------


class DocumentJobRequest(BaseModel):
    """Corps de POST /documents/jobs : générer le CV et la lettre d'une offre."""

    offer_id: str
    # Vrai : régénère même si des documents existent déjà pour l'offre.
    force_new: bool = False


class DocumentJobStepOut(BaseModel):
    kind: str
    status: str  # pending | running | done | error
    doc_id: str | None = None
    error: str | None = None


class DocumentJobOut(BaseModel):
    id: str
    offer_id: str
    offer_title: str
    status: str  # running | done | partial | error
    steps: list[DocumentJobStepOut]
    started_at: str
    finished_at: str | None = None


class OfferDocumentOut(BaseModel):
    """Un document prêt pour une offre : vignette, téléchargement, réouverture."""

    doc_id: str
    kind: str
    template: str
    options: dict[str, bool | str] = {}
    updated_at: str
    page_count: int
    pdf_url: str
    preview_url: str
