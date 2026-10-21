"""Service de RETOUCHE MANUELLE d'un document généré : ouvrir, éditer, recompiler, contrôler.

Boucle courte, sans LLM : une fois le document généré, chaque enregistrement réapplique les
retouches au `render_input` d'origine, relance Typst, et REJOUE les bornes dures (une page,
extractibilité ATS, budget du template, traçabilité). Le LLM n'est rappelé que pour créer un
brouillon qui n'existe pas encore.

Où vit l'état, et pourquoi ici : le texte retouché est l'ENTRÉE de la prochaine compilation,
qui tourne côté serveur. Le brouillon est donc persisté en base (documents/drafts/store) et
le PDF/PNG est un artefact DÉRIVÉ, réécrit à chaque rendu dans le dossier de travail du
document. Le navigateur ne conserve rien d'autoritaire : il affiche une image et poste du
texte.

AUCUN print ici (couche service, cf. project_ctx.md) : le service renvoie des objets, la
présentation appartient à l'API/au front. Les imports LLM restent différés dans les méthodes
pour que l'import du module ne réclame ni clé ni SDK.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from jobtool.core.db import LOCAL_USER_ID
from jobtool.core.experience_base.store import ExperienceBaseMissing, ExperienceBaseRepo
from jobtool.core.rendering.pdf_checks import (
    AtsCheckError,
    check_pdf_ats,
    check_single_page,
    count_pdf_pages,
)
from jobtool.core.rendering.typst_renderer import (
    RenderArtifacts,
    RenderError,
    render_with_fields,
)
from jobtool.documents.drafts import store
from jobtool.documents.drafts.kinds import SourceError, policy_for
from jobtool.documents.drafts.models import DocumentDraft, DocumentKind
from jobtool.documents.drafts.overrides import apply_overrides
from jobtool.services.template_service import (
    DEFAULT_CV_TEMPLATE,
    DEFAULT_LETTER_TEMPLATE,
    LetterTemplateError,
    OptionValue,
    canonical_letter_template,
    describe_cv_template,
    describe_letter_template,
    reusable_cv_template_refs,
)
from jobtool.services.llm_journal_service import GenerationJournal, noop_journal
from jobtool.services.usage_service import UsageRecorder, noop_recorder

# Forme des identifiants émis par ce service (uuid4 tronqué) : sert de garde à la
# construction des chemins de fichiers depuis une valeur d'URL.
_DOC_ID_RE = re.compile(r"[0-9a-f]{1,32}")


class DocumentEditError(RuntimeError):
    """Échec lisible d'une opération d'édition (génération, rendu, contrôle)."""


class DocumentSourceError(DocumentEditError):
    """Source du mode pro inexploitable : JSON illisible, forme invalide, ou ne compile pas.

    Sous-classe distincte parce que la CAUSE est l'entrée, pas le serveur : l'API en fait
    un 422 (à corriger dans l'éditeur) plutôt qu'un 502, sans avoir à lire un message.
    """


class DocumentTemplateError(DocumentEditError):
    """Changement de template refusé : nom inconnu, ou type de document sans ce choix.

    Comme DocumentSourceError, la cause est l'entrée : l'API en fait un 422.
    """


class SourceModeActive(DocumentEditError):
    """Retouche de zones refusée : le mode pro remplace la source du document.

    En mode pro, les zones ne pilotent plus rien (la source réécrite est l'entrée de la
    compilation) ; accepter une retouche la ferait disparaître sans effet visible.
    """


def _reusable_templates(
    kind: DocumentKind, template: str | None
) -> tuple[str, ...] | None:
    """Valeurs du champ `template` d'un brouillon reprenable pour ce choix (None = toutes).

    La clé de reprise du CV est le template ET ses options : un CV « basic » en noir et
    blanc n'est pas repris quand on demande « basic » en bleu. La lettre, elle, reprend son
    dernier brouillon quel que soit son template : changer de template la RECOMPILE (même
    texte, retouches conservées), sans rappeler le modèle.
    """
    if template is None or kind != "cv":
        return None
    return reusable_cv_template_refs(template)


@dataclass(frozen=True)
class EditableField:
    """Un champ retouchable, prêt à être superposé à l'aperçu.

    Les coordonnées sont en POURCENTAGES de la page : l'aperçu peut être affiché à
    n'importe quelle taille, les zones restent alignées sans recalcul côté client.
    """

    id: str
    text: str  # texte actuellement rendu (retouché le cas échéant)
    original: str  # texte issu de la génération, pour pouvoir y revenir
    edited: bool
    page: int
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class DocumentChecks:
    """Verdict des bornes dures sur le document TEL QU'IL EST RENDU maintenant."""

    pages: int
    page_violations: list[str] = field(default_factory=list)
    missing_ats: list[str] = field(default_factory=list)
    overruns: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    # Défauts de mise en page mesurés par le compilateur (titre sur deux lignes, trop de
    # coordonnées…) : signalés, rien n'est coupé. Jugés aussi en mode pro.
    layout_warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Vrai si aucune borne dure n'est franchie."""
        return not (
            self.page_violations
            or self.missing_ats
            or self.overruns
            or self.violations
            or self.layout_warnings
        )


@dataclass(frozen=True)
class DocumentView:
    """Tout ce dont l'écran d'édition a besoin après un rendu."""

    doc_id: str
    offer_id: str
    kind: DocumentKind
    template: str  # template de BASE (ex. « basic », « sobre »)
    options: dict[
        str, OptionValue
    ]  # options résolues du template (vide pour une lettre)
    page_count: int
    fields: list[EditableField]
    checks: DocumentChecks
    edited_field_ids: list[str]
    pdf_path: str
    preview_paths: list[str]
    # Mode pro actif : la source de rendu a été réécrite en entier, les zones ne pilotent
    # plus rien et les bornes de contenu (budget, traçabilité) ne sont plus jugées.
    source_edited: bool = False


@dataclass(frozen=True)
class OfferDocument:
    """Le dernier document d'un type pour une offre, sans ses zones ni ses contrôles :
    de quoi l'afficher en vignette et le télécharger."""

    doc_id: str
    kind: DocumentKind
    template: str
    options: dict[str, OptionValue]
    updated_at: str
    page_count: int


class DocumentEditService:
    """Cycle de vie d'un brouillon retouchable, par-dessus les services de génération.

    `work_root` accueille un sous-dossier par document (PDF + aperçus PNG + copie de
    travail des templates). Il est volontairement sous `output/` : le binaire Typst
    installé par snap ne peut pas écrire dans /tmp (cf. fixture `typst_out_dir`).
    """

    def __init__(
        self,
        base_repo: ExperienceBaseRepo,
        drafts_db: Path | Engine,
        work_root: Path = Path("output/documents"),
        user_id: uuid.UUID = LOCAL_USER_ID,
        record_usage: UsageRecorder = noop_recorder,
        journal: GenerationJournal = noop_journal,
        *,
        profile_key: str,
        api_key: str | None = None,
    ) -> None:
        """`drafts_db` : un moteur (Postgres) ou un chemin SQLite (mono-poste, tests).

        `base_repo` porte la base d'expériences DU COMPTE : un chemin de fichier ferait
        générer tous les CV depuis la même base, quel que soit le compte demandeur.

        `user_id` par défaut = le compte du poste local. Défaut FERMÉ : sur la base
        partagée ce compte n'existe pas, donc un oubli de câblage ne renvoie rien plutôt
        que le document d'un autre. L'API passe toujours le compte authentifié.

        `journal` conserve les échanges avec le modèle pour l'onglet d'administration.
        Par défaut il n'écrit rien : hors API, personne n'a à fournir de base pour générer.

        `profile_key` : profil dont `base_repo` porte la base ("" = principal). Les documents
        sont rangés et retrouvés sous ce profil. OBLIGATOIRE, sans défaut : un défaut ferait
        rouvrir sous un profil le CV généré avec un autre, et le contrôler contre la mauvaise
        base, sans que rien ne le signale.

        `api_key` : clé Mistral DU COMPTE pour les générations (None : celle du serveur).
        """
        self._base_repo = base_repo
        self._api_key = api_key
        self._drafts_db = drafts_db
        self._work_root = Path(work_root)
        self._user_id = user_id
        self._profile_key = profile_key
        self._record_usage = record_usage
        self._journal = journal

    # --- accès stockage ----------------------------------------------------

    def _connect(self):
        """Connexion au moteur fourni (Postgres), ou au fichier SQLite local."""
        return store.open_connection(self._drafts_db)

    def _work_dir(self, doc_id: str) -> Path:
        """Dossier de travail d'un document. Refuse tout id qui n'est pas un id émis ici.

        `doc_id` arrive d'une URL et sert à construire un chemin : sans ce filtre, un id
        comme « ../../data » ferait sortir du dossier de travail. Les ids émis sont des
        hexadécimaux (uuid4 tronqué), donc la borne est stricte plutôt que défensive.
        """
        if not _DOC_ID_RE.fullmatch(doc_id):
            raise DocumentEditError(f"identifiant de document invalide : {doc_id!r}")
        return self._work_root / doc_id

    def pdf_path(self, doc_id: str) -> Path:
        """Chemin du PDF courant (produit du dernier rendu)."""
        return self._work_dir(doc_id) / "document.pdf"

    def preview_path(self, doc_id: str, page: int) -> Path:
        """Chemin de l'aperçu PNG d'une page (1-indexé), produit du dernier rendu."""
        return self._work_dir(doc_id) / f"preview-{int(page)}.png"

    def _load_base(self):
        return self._base_repo.load()

    def _note_call(self, kind: str) -> None:
        """Enregistre la consommation. Seules les GÉNÉRATIONS comptent : rouvrir un
        brouillon ou recompiler une retouche ne rappelle pas le modèle."""
        from jobtool.core.llm.client import MODEL

        self._record_usage(kind, MODEL)

    # --- création / reprise -------------------------------------------------

    def open_document(
        self,
        offer_id: str,
        offer_description: str,
        kind: DocumentKind,
        template: str | None = None,
        *,
        force_new: bool = False,
    ) -> DocumentView:
        """Ouvre l'écran d'édition d'un document : reprend le brouillon, ou en génère un.

        Reprendre par défaut est délibéré : une génération coûte un appel LLM et écraserait
        des retouches. `force_new=True` demande explicitement une nouvelle génération.

        `template` (CV) : None -> on reprend le dernier brouillon quel que soit son template
        (et on génère avec le template par défaut s'il n'y en a pas). Une RÉFÉRENCE
        canonique (`nom?options`, cf. template_service) -> on ne reprend qu'un brouillon de
        CE template avec CES options : rouvrir un CV « compact » quand on vient de choisir
        « basic » en noir et blanc ne respecterait pas le choix.

        `template` (lettre) : nom canonique ou None. Le dernier brouillon est repris quel
        que soit son template ; s'il en porte un autre, il est BASCULÉ sur celui demandé
        (recompilation seule, cf. `set_template`).
        """
        conn = self._connect()
        try:
            if not force_new:
                existing = store.latest_for_offer(
                    conn,
                    self._user_id,
                    offer_id,
                    kind,
                    templates=_reusable_templates(kind, template),
                    profile_key=self._profile_key,
                )
                if existing is not None:
                    if kind == "letter" and template is not None:
                        return self._switch_template(conn, existing, template)
                    return self._render_view(existing)

            default = DEFAULT_CV_TEMPLATE if kind == "cv" else DEFAULT_LETTER_TEMPLATE
            draft = self._generate_draft(
                offer_id, offer_description, kind, template or default
            )
            store.save_draft(conn, self._user_id, draft, profile_key=self._profile_key)
        finally:
            conn.close()
        return self._render_view(draft)

    def _generate_draft(
        self,
        offer_id: str,
        offer_description: str,
        kind: DocumentKind,
        template: str,
    ) -> DocumentDraft:
        """Appelle la génération existante et en retient le `render_input` recompilable."""
        doc_id = uuid.uuid4().hex[:16]
        scratch = self._work_dir(doc_id)
        scratch.mkdir(parents=True, exist_ok=True)
        timestamp = store.now_iso()

        if kind == "letter":
            template = canonical_letter_template(template)

        # Journal des échanges : il enveloppe l'appel au modèle, et lui seul. Le rendu et
        # les contrôles qui suivent n'ont rien à y faire, et une génération qui échoue
        # laisse quand même sa trace (avec son erreur).
        with self._journal.record(kind, offer_id=offer_id, template=template):
            if kind == "cv":
                render_input = self._generate_cv_input(
                    offer_description, template, scratch / "generated.pdf"
                )
            else:
                render_input = self._generate_letter_input(
                    offer_description, scratch / "generated.pdf"
                )

        draft = DocumentDraft(
            doc_id=doc_id,
            offer_id=offer_id,
            kind=kind,
            template=template,
            render_input=render_input,
            overrides={},
            created_at=timestamp,
            updated_at=timestamp,
        )

        # Relevé UNIQUE du texte généré de chaque champ : c'est le compilateur qui dit ce
        # qu'il a écrit (une seule définition de « champ », celle des templates), et le
        # résultat est figé avec le render_input dont il dépend.
        originals = self._compile(draft, render_input, scratch / "_original")
        return draft.model_copy(
            update={"original_texts": {box.id: box.text for box in originals.fields}}
        )

    def _generate_cv_input(
        self, offer_description: str, template: str, out_pdf: Path
    ) -> dict:
        from jobtool.documents.cv.profiles import get_profile
        from jobtool.core.llm.client import using_api_key
        from jobtool.documents.cv.render import build_render_input
        from jobtool.services.batch_service import process_generate

        base = self._load_base()
        # Profil EFFECTIF : les options de mise en page (accroche…) y sont appliquées, pour
        # que le remplissage mesure la page telle qu'elle sera rendue.
        profile = get_profile(template)
        self._note_call("cv")
        with using_api_key(self._api_key):
            result = process_generate(base, profile, offer_description, out_pdf, template)
        if result.cv is None:
            raise DocumentEditError(result.error or "génération du CV échouée")
        options = describe_cv_template(template).options
        return build_render_input(base, result.cv, profile, options)

    def _generate_letter_input(self, offer_description: str, out_pdf: Path) -> dict:
        from jobtool.core.llm.client import using_api_key
        from jobtool.documents.cover_letter.render import build_render_input
        from jobtool.services.batch_service import process_letter

        base = self._load_base()
        self._note_call("letter")
        with using_api_key(self._api_key):
            result = process_letter(base, offer_description, out_pdf)
        if result.render is None:
            raise DocumentEditError(result.error or "génération de la lettre échouée")
        return build_render_input(result.render)

    # --- documents d'une offre ------------------------------------------------

    def default_templates(self) -> tuple[str | None, str | None]:
        """(référence du CV, template de lettre) choisis dans le profil ; None = défaut."""
        try:
            prefs = self._load_base().documents
        except ExperienceBaseMissing:
            return None, None
        return prefs.cv_template, prefs.letter_template

    def offer_documents(self, offer_id: str) -> list[OfferDocument]:
        """Dernier CV et dernière lettre de l'offre (ceux que rouvre le tiroir).

        Aucune génération. Le document n'est recompilé que si son aperçu manque sur le
        disque (dossier de travail purgé) : la vignette doit pouvoir s'afficher.
        """
        found: list[OfferDocument] = []
        conn = self._connect()
        try:
            drafts = [
                store.latest_for_offer(
                    conn, self._user_id, offer_id, kind, profile_key=self._profile_key
                )
                for kind in ("cv", "letter")
            ]
        finally:
            conn.close()
        for draft in drafts:
            if draft is None:
                continue
            pages = self._preview_count(draft.doc_id)
            if pages == 0:
                pages = self._render_view(draft).page_count
            if draft.kind == "cv":
                template, options = describe_cv_template(draft.template)
            else:
                template, options = describe_letter_template(draft.template), {}
            found.append(
                OfferDocument(
                    doc_id=draft.doc_id,
                    kind=draft.kind,
                    template=template,
                    options=options,
                    updated_at=draft.updated_at,
                    page_count=pages,
                )
            )
        return found

    def _preview_count(self, doc_id: str) -> int:
        count = 0
        while self.preview_path(doc_id, count + 1).is_file():
            count += 1
        return count

    # --- lecture / édition --------------------------------------------------

    def get_document(self, doc_id: str) -> DocumentView | None:
        """Recompile et renvoie l'état courant d'un brouillon, ou None s'il est inconnu."""
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
        finally:
            conn.close()
        return None if draft is None else self._render_view(draft)

    # --- template : changer de mise en page sans régénérer ----------------------

    def set_template(self, doc_id: str, template: str) -> DocumentView | None:
        """Recompile un document avec un autre template ; None si le document est inconnu.

        Aucun appel au modèle : le texte (génération, retouches de zones, source du mode
        pro) est conservé tel quel, seule la mise en page change. Réservé à la lettre, dont
        tous les templates lisent les mêmes données ; un CV change de budget avec son
        template, il se régénère. Rien n'est enregistré si le document ne compile pas.
        """
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
            if draft is None:
                return None
            if draft.kind != "letter":
                raise DocumentTemplateError(
                    "changer de template sans régénérer n'est possible que pour une lettre"
                )
            return self._switch_template(conn, draft, template)
        finally:
            conn.close()

    def _switch_template(
        self, conn, draft: DocumentDraft, template: str
    ) -> DocumentView:
        """Compile `draft` avec `template`, puis n'enregistre le nom qu'en cas de succès."""
        try:
            name = canonical_letter_template(template)
        except LetterTemplateError as exc:
            raise DocumentTemplateError(str(exc)) from exc
        if name == canonical_letter_template(draft.template):
            return self._render_view(draft)
        candidate = draft.model_copy(update={"template": name})
        view = self._render_view(candidate)
        store.set_template(conn, self._user_id, draft.doc_id, name)
        return view

    # --- mode pro : la source de rendu, en entier ------------------------------

    def get_source(self, doc_id: str) -> tuple[str, bool] | None:
        """(source JSON indentée, mode pro actif ?), ou None si le document est inconnu.

        La source exposée est EXACTEMENT l'entrée de la dernière compilation : la source
        réécrite si le mode pro est actif, sinon l'origine AVEC les retouches de zones.
        """
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
        finally:
            conn.close()
        if draft is None:
            return None
        source = json.dumps(self._current_data(draft), ensure_ascii=False, indent=2)
        return source, draft.edited_input is not None

    def set_source(self, doc_id: str, source: str) -> DocumentView | None:
        """Remplace la source de rendu par `source` (JSON), recompile et recontrôle.

        Rien n'est enregistré tant que la source n'a pas COMPILÉ : un JSON illisible, une
        forme invalide ou une erreur Typst lèvent DocumentSourceError et laissent le
        document exactement dans son dernier état valide, aperçu compris.
        """
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
            if draft is None:
                return None
            data = self._parse_source(draft, source)
            candidate = draft.model_copy(update={"edited_input": data})
            try:
                view = self._render_view(candidate)
            except DocumentEditError as exc:
                raise DocumentSourceError(f"la source ne compile pas : {exc}") from exc
            store.set_edited_input(conn, self._user_id, doc_id, data)
        finally:
            conn.close()
        return view

    def reset_source(self, doc_id: str) -> DocumentView | None:
        """Quitte le mode pro : retour à la génération ET aux retouches de zones, intactes."""
        conn = self._connect()
        try:
            if (
                store.get_draft(
                    conn, self._user_id, doc_id, profile_key=self._profile_key
                )
                is None
            ):
                return None
            store.set_edited_input(conn, self._user_id, doc_id, None)
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
        finally:
            conn.close()
        return None if draft is None else self._render_view(draft)

    @staticmethod
    def _parse_source(draft: DocumentDraft, source: str) -> dict:
        """JSON tapé à la main -> source validée et complétée pour CE type de document."""
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            raise DocumentSourceError(
                f"JSON invalide (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}"
            ) from exc
        if not isinstance(data, dict):
            raise DocumentSourceError(
                f"la source doit être un objet JSON {{…}}, reçu : {type(data).__name__}"
            )
        try:
            return policy_for(draft.kind).normalize_source(data)
        except SourceError as exc:
            raise DocumentSourceError(str(exc)) from exc

    @staticmethod
    def _current_data(draft: DocumentDraft) -> dict:
        """L'entrée de compilation : source du mode pro, ou origine + retouches de zones.

        UN SEUL point de décision, pour que le rendu, les contrôles et la source exposée
        ne puissent pas partir de trois états différents.
        """
        policy = policy_for(draft.kind)
        if draft.edited_input is not None:
            return policy.complete(draft.edited_input)
        return policy.complete(
            apply_overrides(draft.kind, draft.render_input, draft.overrides)
        )

    @staticmethod
    def _refuse_in_source_mode(draft: DocumentDraft) -> None:
        if draft.edited_input is not None:
            raise SourceModeActive(
                "mode pro actif : modifiez la source, ou revenez aux données générées "
                "pour retoucher les zones"
            )

    # --- retouches de zones -------------------------------------------------

    def set_fields(self, doc_id: str, edits: dict[str, str]) -> DocumentView | None:
        """Applique des retouches (id -> texte) puis recompile et recontrôle.

        Une valeur vide ou identique au texte d'origine RETIRE la retouche : le champ
        revient au texte généré plutôt que de figer un doublon de l'original.
        """
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
            if draft is None:
                return None
            self._refuse_in_source_mode(draft)

            overrides = dict(draft.overrides)
            for field_id, text in edits.items():
                if not text.strip() or text == draft.original_texts.get(field_id):
                    overrides.pop(field_id, None)
                else:
                    overrides[field_id] = text

            store.set_overrides(conn, self._user_id, doc_id, overrides)
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
        finally:
            conn.close()

        return None if draft is None else self._render_view(draft)

    def reset_fields(self, doc_id: str, field_ids: list[str]) -> DocumentView | None:
        """Rend leur texte généré aux champs désignés (liste vide = tout réinitialiser)."""
        conn = self._connect()
        try:
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
            if draft is None:
                return None
            self._refuse_in_source_mode(draft)
            overrides = dict(draft.overrides)
            if field_ids:
                for field_id in field_ids:
                    overrides.pop(field_id, None)
            else:
                overrides = {}
            store.set_overrides(conn, self._user_id, doc_id, overrides)
            draft = store.get_draft(
                conn, self._user_id, doc_id, profile_key=self._profile_key
            )
        finally:
            conn.close()

        return None if draft is None else self._render_view(draft)

    def delete_work_dir(self, doc_id: str) -> None:
        """Supprime les artefacts d'un document (PDF/aperçus). Le brouillon reste en base."""
        shutil.rmtree(self._work_dir(doc_id), ignore_errors=True)

    # --- rendu + contrôles --------------------------------------------------

    def _compile(
        self, draft: DocumentDraft, data: dict, work_dir: Path
    ) -> RenderArtifacts:
        policy = policy_for(draft.kind)
        try:
            template = policy.template_path(draft.template)
            return render_with_fields(template, policy.complete(data), work_dir)
        except (RenderError, LetterTemplateError) as exc:
            raise DocumentEditError(str(exc)) from exc

    def _render_view(self, draft: DocumentDraft) -> DocumentView:
        """Compile l'état courant du brouillon et assemble la vue complète pour l'écran."""
        data = self._current_data(draft)
        artifacts = self._compile(draft, data, self._work_dir(draft.doc_id))
        originals = draft.original_texts

        page_width = artifacts.page_width_pt or 1.0
        page_height = artifacts.page_height_pt or 1.0
        fields = [
            EditableField(
                id=box.id,
                text=box.text,
                original=originals.get(box.id, box.text),
                edited=box.id in draft.overrides,
                page=box.page,
                left=100.0 * box.x / page_width,
                top=100.0 * box.y / page_height,
                width=100.0 * box.w / page_width,
                height=100.0 * box.h / page_height,
            )
            for box in artifacts.fields
        ]

        if draft.kind == "cv":
            template, options = describe_cv_template(draft.template)
        else:
            template, options = describe_letter_template(draft.template), {}
        return DocumentView(
            doc_id=draft.doc_id,
            offer_id=draft.offer_id,
            kind=draft.kind,
            template=template,
            options=options,
            page_count=len(artifacts.previews),
            fields=fields,
            checks=self._run_checks(draft, data, artifacts),
            edited_field_ids=sorted(draft.overrides),
            pdf_path=artifacts.pdf_path,
            preview_paths=artifacts.previews,
            source_edited=draft.edited_input is not None,
        )

    def _run_checks(
        self, draft: DocumentDraft, data: dict, artifacts: RenderArtifacts
    ) -> DocumentChecks:
        """Rejoue les bornes dures sur le PDF et le texte ÉDITÉS (jamais sur l'original).

        En mode pro, seules les mesures du PDF restent jugées (une page, texte
        extractible) : budget et traçabilité décrivent un document assemblé depuis la base
        par l'outil ; les opposer à une source écrite à la main n'aurait pas de sens.
        """
        pdf_path = artifacts.pdf_path
        policy = policy_for(draft.kind)
        base = None
        profile = None
        source_mode = draft.edited_input is not None
        # Charger la base d'expériences coûte un aller-retour : on ne le fait que pour les
        # documents dont le contenu s'y compare (le CV). La lettre n'en a pas besoin.
        if policy.needs_experience_base and not source_mode:
            from jobtool.documents.cv.profiles import get_profile

            try:
                base = self._load_base()
                profile = get_profile(draft.template or "classic")
            except Exception:  # noqa: BLE001 — base illisible : on rend sans ces checks
                base, profile = None, None

        violations, overruns = (
            ([], []) if source_mode else policy.content_issues(data, base, profile)
        )

        try:
            pages = count_pdf_pages(pdf_path)
            page_violations = check_single_page(pdf_path)
            missing = check_pdf_ats(pdf_path, policy.ats_targets(data))
        except AtsCheckError as exc:
            raise DocumentEditError(f"contrôle PDF : {exc}") from exc

        return DocumentChecks(
            pages=pages,
            page_violations=page_violations,
            missing_ats=missing,
            overruns=overruns,
            violations=[str(v) for v in violations],
            layout_warnings=list(artifacts.warnings),
        )
