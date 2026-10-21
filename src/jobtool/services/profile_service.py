"""Service de lecture/écriture de la base d'expériences sous une FORME PROFIL.

Couche SERVICE (printless) : SEUL endroit qui traduit entre le modèle interne
`ExperienceBase` (utilisé par la génération CV/lettre) et une forme « profil » plate,
pratique pour le front. Aucune sortie terminal, aucun réseau.

Il ne sait PAS où la base est rangée : il reçoit un dépôt (`ExperienceBaseRepo`), qui est un
fichier YAML en mono-poste et une ligne cloisonnée par compte sur la base partagée. Ce
service lisait auparavant un chemin de fichier, donc le MÊME fichier pour tous les comptes.

Frontière d'architecture (project_ctx.md) : l'API appelle ce service ; le front appelle l'API. La
logique de TRADUCTION (et la PRÉSERVATION des champs non édités) vit ICI, pas dans l'API ni le
front.

Préservation (invariant) : le profil n'expose qu'une PARTIE de la base. À l'écriture, les champs
NON édités par le front sont conservés depuis la base existante : `education`, `interests`,
`profile.location/links/accroche(_template)`, et le drapeau `pinned` de chaque expérience. La
génération (qui relit `ExperienceBase`) n'est donc jamais cassée.

Simplifications ASSUMÉES (mapping plat) :
  - identité : la base stocke un seul `name` ; on le scinde prénom/nom sur le 1er espace, et on
    recompose « prénom nom » à l'écriture (acceptable pour un outil perso) ;
  - langue : `{name, level}` <-> chaîne « Nom — Niveau » (séparateur tiret cadratin) ;
  - écrire reformate le YAML (commentaires/disposition d'origine perdus) ; les DONNÉES, elles,
    sont préservées.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from jobtool.core.experience_base.loader import BaseLoadError
from jobtool.core.experience_base.models import (
    Achievement,
    DocumentPreferences,
    Experience,
    ExperienceBase,
    ExperienceType,
    Language,
    Profile,
)
from jobtool.core.experience_base.photo import normalize_photo
from jobtool.core.experience_base.photo_store import PhotoStore, StoredPhoto
from jobtool.core.experience_base.store import ExperienceBaseMissing, ExperienceBaseRepo
from jobtool.services.template_service import (
    LetterTemplateError,
    OptionValue,
    TemplateOptionError,
    canonical_cv_template,
    canonical_letter_template,
    describe_cv_template,
)

# --- Forme « profil » exposée au front (modèles pydantic) -------------------


class ProfileRealisationDTO(BaseModel):
    """Une réalisation (achievement) sous la forme attendue par le front."""

    id: str
    text: str = ""
    skills: list[str] = []


class ProfileExperienceDTO(BaseModel):
    """Une expérience/projet à plat (clés alignées sur le front)."""

    id: str
    titre: str = ""
    org: str = ""
    type: str = "job"  # valeur d'ExperienceType : job|internship|school_project|personal_project
    debut: str = ""  # "YYYY-MM"
    fin: str = ""  # "YYYY-MM" ou "present"
    resume: str = ""
    realisations: list[ProfileRealisationDTO] = []


class ProfileDTO(BaseModel):
    """Profil complet échangé avec le front (GET renvoie, PUT reçoit)."""

    nom: str = ""
    prenom: str = ""
    telephone: str = ""
    email: str = ""
    soft: list[str] = []
    hard: list[str] = []
    langues: list[str] = []
    experiences: list[ProfileExperienceDTO] = []
    # Présence d'une photo, jamais ses octets : l'image se récupère par sa propre route,
    # pour ne pas alourdir chaque lecture du profil de ~60 Kio de base64.
    a_photo: bool = False
    # Templates par défaut des documents ("" = celui du serveur) et lettre type. Le CV
    # porte ses options à part ; la base, elle, range la référence canonique.
    template_cv: str = ""
    options_cv: dict[str, OptionValue] = {}
    template_lettre: str = ""
    lettre_exemple: str = ""


class ProfileError(Exception):
    """YAML mal formé ou profil invalide (message lisible, sans secret)."""


# --- Helpers de traduction (purs) -------------------------------------------

_LANG_SEP = " — "  # tiret cadratin : séparateur « Nom — Niveau » côté front


def _split_name(name: str) -> tuple[str, str]:
    """« Prénom Nom » -> (prénom, nom) sur le 1er espace ; nom vide si un seul mot."""
    parts = name.strip().split(" ", 1)
    prenom = parts[0] if parts else ""
    nom = parts[1] if len(parts) > 1 else ""
    return prenom, nom


def _lang_to_str(lang: Language) -> str:
    return f"{lang.name}{_LANG_SEP}{lang.level}" if lang.level else lang.name


def _str_to_lang(value: str) -> Language:
    """« Nom — Niveau » -> Language ; sans séparateur -> niveau vide."""
    for sep in (_LANG_SEP, "—", " - ", "–", "-"):
        if sep in value:
            name, level = value.split(sep, 1)
            return Language(name=name.strip(), level=level.strip())
    return Language(name=value.strip(), level="")


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(racine)"
        parts.append(f"{loc} : {err['msg']}")
    return "profil invalide : " + " ; ".join(parts)


# --- Service ----------------------------------------------------------------


class ProfileService:
    """Lit/écrit la base d'expériences d'un compte, projetée en `ProfileDTO`."""

    def __init__(self, repo: ExperienceBaseRepo, photos: PhotoStore | None = None):
        """`repo` porte le compte ET l'emplacement : ce service n'en connaît ni l'un ni
        l'autre. C'est ce qui l'empêche de lire la base d'un autre compte.

        `photos` est le dépôt de la photo du MÊME compte. Optionnel pour les appelants qui
        ne touchent qu'au texte (CLI, tests de traduction) : les méthodes photo lèvent
        alors clairement au lieu de travailler sur le mauvais compte.
        """
        self._repo = repo
        self._photos = photos

    # --- Lecture ------------------------------------------------------------

    def read(self) -> ProfileDTO:
        """Charge la base et la projette en profil. Lève FileNotFoundError / ProfileError."""
        return self._to_dto(self._parse())

    # --- Écriture -----------------------------------------------------------

    def write(self, dto: ProfileDTO) -> ProfileDTO:
        """Applique le profil sur la base (champs non édités PRÉSERVÉS), écrit, relit.

        Lève ProfileError si la base résultante est invalide (dates, ids en double, etc.).
        """
        try:
            existing = self._parse()
        except ExperienceBaseMissing:
            existing = None  # compte neuf : rien à préserver, on crée
        base = self._to_base(dto, existing)
        self._repo.save(base)
        return self._to_dto(base)

    # --- Photo --------------------------------------------------------------
    #
    # Deux écritures doivent rester cohérentes : l'image dans `profile_photos`, et son
    # EMPREINTE dans `Profile.photo`. C'est pourquoi elles sont pilotées ici et non par
    # deux appels séparés depuis l'API — le rendu ne trouve la photo que si les deux
    # concordent (cf. PhotoStore.load_if).

    def set_photo(self, raw: bytes) -> ProfileDTO:
        """Valide, normalise et range la photo ; met à jour le profil ; le renvoie.

        Lève `PhotoError` (image refusée) ou `ExperienceBaseMissing` (aucun profil à
        rattacher : on ne crée pas une base d'expériences à partir d'une photo).
        """
        photos = self._require_photos()
        sha256, jpeg = normalize_photo(raw)
        base = self._parse()
        photos.save(StoredPhoto(sha256, jpeg))
        self._repo.save(self._with_photo(base, sha256))
        return self._to_dto(self._parse())

    def delete_photo(self) -> ProfileDTO:
        """Retire la photo et l'empreinte du profil. Idempotent."""
        photos = self._require_photos()
        base = self._parse()
        photos.delete()
        self._repo.save(self._with_photo(base, None))
        return self._to_dto(self._parse())

    def photo_bytes(self) -> bytes | None:
        """Les octets JPEG de la photo du compte, ou None s'il n'en a pas."""
        photos = self._require_photos()
        stored = photos.load()
        return None if stored is None else stored.jpeg

    def photo_for_render(self) -> tuple[str, bytes] | None:
        """(empreinte, JPEG) à passer au rendu, ou None.

        Passe par `load_if` : si l'empreinte du profil ne correspond plus à l'image
        stockée, on rend SANS photo plutôt qu'avec une image que l'utilisateur croyait
        avoir remplacée.
        """
        if self._photos is None:
            return None
        try:
            base = self._parse()
        except ExperienceBaseMissing:
            return None
        stored = self._photos.load_if(base.profile.photo)
        return None if stored is None else (stored.sha256, stored.jpeg)

    def _require_photos(self) -> PhotoStore:
        if self._photos is None:
            raise ProfileError("ce service n'a pas été câblé au dépôt de photos")
        return self._photos

    @staticmethod
    def _with_photo(base: ExperienceBase, sha256: str | None) -> ExperienceBase:
        """La même base, avec l'empreinte de photo remplacée (rien d'autre ne bouge)."""
        return base.model_copy(
            update={"profile": base.profile.model_copy(update={"photo": sha256})}
        )

    # --- Accès au dépôt -----------------------------------------------------

    def _parse(self) -> ExperienceBase:
        """Base du compte. Laisse passer l'absence, traduit l'illisible en `ProfileError`.

        L'absence garde son type d'origine (`ExperienceBaseMissing`, un `FileNotFoundError`)
        parce qu'elle ne veut pas dire la même chose qu'une base cassée : l'une est un 404,
        l'autre un 400.
        """
        try:
            return self._repo.load()
        except ExperienceBaseMissing:
            raise
        except BaseLoadError as exc:
            raise ProfileError(str(exc)) from exc

    # --- Traductions modèle <-> DTO -----------------------------------------

    def _to_dto(self, base: ExperienceBase) -> ProfileDTO:
        prenom, nom = _split_name(base.profile.name)
        return ProfileDTO(
            nom=nom,
            prenom=prenom,
            telephone=base.profile.phone or "",
            email=base.profile.email,
            soft=list(base.soft_skills),
            hard=list(base.hard_skills),
            langues=[_lang_to_str(lang) for lang in base.languages],
            a_photo=bool(base.profile.photo),
            **self._documents_to_dto(base.documents),
            experiences=[
                ProfileExperienceDTO(
                    id=exp.id,
                    titre=exp.title,
                    org=exp.organization,
                    type=exp.type.value,
                    debut=exp.start,
                    fin=exp.end,
                    resume=exp.summary or "",
                    realisations=[
                        ProfileRealisationDTO(
                            id=ach.id, text=ach.text, skills=list(ach.skills)
                        )
                        for ach in exp.achievements
                    ],
                )
                for exp in base.experiences
            ],
        )

    @staticmethod
    def _documents_to_dto(prefs: DocumentPreferences) -> dict:
        cv = describe_cv_template(prefs.cv_template) if prefs.cv_template else None
        return {
            "template_cv": cv.name if cv else "",
            "options_cv": dict(cv.options) if cv else {},
            "template_lettre": prefs.letter_template or "",
            "lettre_exemple": prefs.letter_example or "",
        }

    @staticmethod
    def _documents_to_base(dto: ProfileDTO) -> DocumentPreferences:
        """Réglages des documents, VALIDÉS : un template inconnu est refusé ici (400)
        plutôt qu'à la première génération, souvent lancée en arrière-plan."""
        try:
            cv = (
                canonical_cv_template(dto.template_cv, dto.options_cv)
                if dto.template_cv
                else None
            )
            letter = (
                canonical_letter_template(dto.template_lettre)
                if dto.template_lettre
                else None
            )
        except (TemplateOptionError, LetterTemplateError) as exc:
            raise ProfileError(f"template par défaut invalide : {exc}") from exc
        return DocumentPreferences(
            cv_template=cv,
            letter_template=letter,
            letter_example=dto.lettre_exemple.strip() or None,
        )

    def _to_base(
        self, dto: ProfileDTO, existing: ExperienceBase | None
    ) -> ExperienceBase:
        ep = existing.profile if existing else None
        profile = Profile(
            name=f"{dto.prenom} {dto.nom}".strip(),
            email=dto.email,
            phone=(dto.telephone or None),
            # Champs NON édités par le front : préservés depuis la base existante.
            location=(ep.location if ep else None),
            links=(ep.links if ep else None),
            accroche=(ep.accroche if ep else None),
            accroche_template=(ep.accroche_template if ep else None),
            # La photo n'est pas éditée par cet écran : sans cette ligne, enregistrer le
            # profil effacerait l'empreinte, et le CV perdrait la photo en silence.
            photo=(ep.photo if ep else None),
        )
        pinned = {e.id: e.pinned for e in (existing.experiences if existing else [])}
        try:
            experiences: list[Experience] = []
            for ed in dto.experiences:
                try:
                    etype = ExperienceType(ed.type)
                except ValueError:
                    raise ProfileError(
                        f'type d\'expérience invalide : "{ed.type}" '
                        f"(attendus : {', '.join(t.value for t in ExperienceType)})"
                    )
                experiences.append(
                    Experience(
                        id=ed.id,
                        type=etype,
                        title=ed.titre,
                        organization=ed.org,
                        start=ed.debut,
                        end=ed.fin,
                        summary=(ed.resume or None),
                        achievements=[
                            Achievement(id=a.id, text=a.text, skills=list(a.skills))
                            for a in ed.realisations
                        ],
                        pinned=pinned.get(ed.id, False),  # drapeau préservé
                    )
                )
            return ExperienceBase(
                profile=profile,
                education=list(existing.education) if existing else [],
                languages=[_str_to_lang(s) for s in dto.langues if s.strip()],
                hard_skills=list(dto.hard),
                soft_skills=list(dto.soft),
                interests=list(existing.interests) if existing else [],
                experiences=experiences,
                documents=self._documents_to_base(dto),
            )
        except ValidationError as exc:
            raise ProfileError(_format_validation(exc))
