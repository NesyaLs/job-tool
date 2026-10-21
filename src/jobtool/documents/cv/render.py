"""Assemblage des données d'un CV vers le format attendu par le template, + rendu.

Partie SPÉCIFIQUE CV du rendu : on résout identité/formation/langues (EN DUR, base)
et expériences/skills (générés, TailoredCV) en un dict, puis on délègue la
compilation Typst à core/rendering. Le choix du template (classic|compact|basic) est ici,
et les templates vivent à côté de ce module (cv/templates/).

Le rendu reste déterministe : mêmes données -> même render_input.json -> même PDF.
"""

from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from jobtool.core.experience_base.models import ExperienceBase
from jobtool.core.experience_base.photo import photo_filename
from jobtool.core.rendering.typst_renderer import (
    RenderError,
    render_typst,
    render_typst_isolated,
)
from jobtool.documents.cv.profiles import (
    DEFAULT_PROFILE,
    OptionValue,
    TemplateOptionError,
    TemplateProfile,
    get_profile,
    parse_template_ref,
)
from jobtool.documents.cv.schema import TailoredCV

# Templates CV = données de paquet : résolus via importlib.resources (robuste à
# l'emplacement d'installation), jamais via un chemin relatif fragile.
TEMPLATES_DIR = Path(str(files("jobtool.documents.cv").joinpath("templates")))

DEFAULT_TEMPLATE = "classic"

_logger = logging.getLogger(__name__)

# Domaines reconnus -> champ de contact dédié (template basic : github / linkedin). Une
# URL d'un autre domaine va dans `site`. Comparaison sur la FIN du nom d'hôte, pour
# couvrir les sous-domaines (www., wwww., fr.) sans lister chaque variante.
_LINK_FIELDS_BY_DOMAIN = {"github.com": "github", "linkedin.com": "linkedin"}


def _truncate_skills_line(items: list[str], max_chars: int) -> list[str]:
    """Préfixe de `items` tel que ', '.join(préfixe) <= max_chars.

    Le LLM classe les éléments par pertinence décroissante ; on garde les premiers
    qui tiennent dans la ligne. Le troncage est déterministe (pas de jugement LLM
    au rendu), ce qui rend la vérification de longueur dans check_budget inutile.
    """
    kept: list[str] = []
    for item in items:
        candidate = ", ".join(kept + [item])
        if len(candidate) <= max_chars:
            kept.append(item)
        else:
            break
    return kept


def _strip_scheme(url: str) -> str:
    """URL sans son schéma http(s):// (le template basic ajoute `https://` lui-même)."""
    stripped = url.strip()
    for scheme in ("https://", "http://"):
        if stripped.lower().startswith(scheme):
            return stripped[len(scheme) :]
    return stripped


def _split_links(links: list[str]) -> dict[str, str]:
    """Répartit des URL complètes en champs fixes {github, linkedin, site}, sans schéma.

    Règle déterministe : github.com -> `github`, linkedin.com -> `linkedin`, tout autre
    domaine -> `site`. Un seul lien par champ : le PREMIER l'emporte, les suivants sont
    ignorés avec un avertissement dans les logs (le template n'a qu'une case par champ ;
    on ne les concatène pas, le lien cliquable serait faux).
    """
    fields = {"github": "", "linkedin": "", "site": ""}
    for url in links:
        value = _strip_scheme(url)
        if not value:
            continue
        host = urlsplit("//" + value).hostname or ""
        name = next(
            (
                field
                for domain, field in _LINK_FIELDS_BY_DOMAIN.items()
                if host == domain or host.endswith("." + domain)
            ),
            "site",
        )
        if fields[name]:
            _logger.warning(
                "lien ignoré (champ %s déjà occupé par %s) : %s",
                name,
                fields[name],
                url,
            )
            continue
        fields[name] = value
    return fields


# Un SEUL template Typst de base (cv_compact.typ) sert les 4 presets via 2 blocs
# conditionnels (show_soft_skills / show_accroche) ; pas de duplication en 4 fichiers.
# cv_classic.typ reste un layout alternatif baseline, inchangé.
_TEMPLATES = {
    "classic": "cv_classic.typ",  # mesure de largeur (org inline si ça tient)
    "compact": "cv_compact.typ",  # règle fixe, sans measure (compilation instantanée)
    "compact-soft": "cv_compact.typ",  # baseline + section soft skills
    "compact-ctx": "cv_compact.typ",  # baseline + phrase d'accroche
    "compact-soft-ctx": "cv_compact.typ",  # baseline + soft skills + accroche
    "basic": "cv_basic.typ",  # package @preview/basic-resume (marges du package)
    "modern_cv": "cv_modern_cv.typ",  # package @preview/modern-cv (port d'Awesome-CV)
    "cobalt": "cv_cobalt.typ",  # recopie MIT du modèle @preview/cobalt-cv (barre latérale)
    "lavandula": "cv_lavandula.typ",  # package @preview/lavandula (barre colorée, pastilles)
    "metronic": "cv_metronic.typ",  # package @preview/metronic, mise en page RÉÉCRITE
}


def accroche_is_adaptable(base: ExperienceBase, tmpl_profile: TemplateProfile) -> bool:
    """Vrai si le LLM doit choisir le {focus} (accroche ADAPTABLE active sur ce template).

    Adaptable = show_accroche actif ET un template présent. Si les deux formes coexistent
    (accroche fixe ET template), le template est prioritaire -> adaptable (cf. _resolve_accroche).
    """
    return bool(
        tmpl_profile.show_accroche and base.profile.accroche_template is not None
    )


def validate_accroche_config(
    base: ExperienceBase, tmpl_profile: TemplateProfile
) -> None:
    """Garde-fou déterministe de la config d'accroche (lève RenderError, n'invente rien).

    Ne vérifie rien si show_accroche est désactivé. Sinon :
      - template présent SANS le trou {focus} -> erreur (le trou est obligatoire en adaptable) ;
      - ni accroche ni template -> erreur (rien à afficher, on ne fabrique pas de phrase).
    """
    if not tmpl_profile.show_accroche:
        return
    prof = base.profile
    if prof.accroche_template is not None:
        if "{focus}" not in prof.accroche_template:
            raise RenderError(
                "accroche_template doit contenir le trou {focus} (mode adaptable) ; "
                f"reçu : {prof.accroche_template!r}"
            )
        return
    if prof.accroche is not None:
        return
    raise RenderError(
        "show_accroche=true mais la base ne définit ni 'accroche' (fixe) ni "
        "'accroche_template' (adaptable) : rien à afficher."
    )


def _resolve_accroche(
    base: ExperienceBase, tmpl_profile: TemplateProfile, focus: str
) -> str:
    """Texte d'accroche à afficher (chaîne vide si désactivée). Applique la règle fixe/adaptable.

    Règle déterministe quand les deux formes coexistent : accroche_template PRIORITAIRE.
    """
    validate_accroche_config(base, tmpl_profile)
    if not tmpl_profile.show_accroche:
        return ""
    prof = base.profile
    if prof.accroche_template is not None:
        return prof.accroche_template.replace("{focus}", focus.strip())
    return prof.accroche or ""  # non-None garanti par validate_accroche_config


def _build_render_input(
    base: ExperienceBase,
    cv: TailoredCV,
    tmpl_profile: TemplateProfile | None = None,
    options: dict[str, OptionValue] | None = None,
    photo_sha256: str | None = None,
) -> dict:
    """Assemble les données du rendu.

    Zone EN DUR (depuis la base, court-circuit du LLM) : identité + formation + langues.
    Zone GÉNÉRÉE (depuis le TailoredCV) : expériences tracées + soft/hard skills + interests.
    Les titre/orga/dates des expériences sont résolus depuis la base via source_id.
    Les lignes de compétences sont tronquées déterministiquement (hard/interests à
    max_skills_line_chars, soft à max_soft_skills_line_chars). On expose aussi les options
    d'affichage (show_soft_skills / show_accroche) et l'accroche résolue, lues par le template.
    """
    tmpl_profile = tmpl_profile or DEFAULT_PROFILE
    experiences_by_id = {exp.id: exp for exp in base.experiences}
    profile = base.profile

    contact_bits = [profile.email]
    if profile.phone:
        contact_bits.append(profile.phone)
    if profile.location:
        contact_bits.append(profile.location)

    education = [
        {
            "degree": edu.degree,
            "school": edu.school,
            "dates": f"{edu.start} – {edu.end}",
        }
        for edu in base.education
    ]

    experiences = []
    for tailored_exp in cv.experiences:
        source = experiences_by_id.get(tailored_exp.source_experience_id)
        if source is None:
            # Ne devrait pas arriver sur un CV validé par check_traceability ;
            # on échoue clairement plutôt que de produire un PDF tronqué.
            raise RenderError(
                f"expérience inconnue dans la base : {tailored_exp.source_experience_id}"
            )
        experiences.append(
            {
                # type : donnée déjà présente dans la base, passée pour que le template
                # sépare EXPÉRIENCE (internship/job) et PROJETS (school/personal).
                "type": source.type.value,
                # id d'origine de l'expérience : non lu par les templates, mais il rend
                # le render_input RÉVERSIBLE en TailoredCV (cf. drafts/checks.py), donc
                # rejouable dans les MÊMES contrôles après une retouche manuelle.
                "id": tailored_exp.source_experience_id,
                "title": source.title,
                "organization": source.organization,
                "dates": f"{source.start} – {source.end}",
                # Chaque puce porte son id de TRAÇABILITÉ (l'achievement reformulé) :
                # c'est aussi son identifiant de champ éditable côté retouche manuelle.
                # Un seul système d'ids, celui qui existait déjà.
                "bullets": [
                    {"id": bullet.source_id, "text": bullet.text}
                    for bullet in tailored_exp.bullets
                ],
            }
        )

    languages = [{"name": lang.name, "level": lang.level} for lang in base.languages]

    return {
        "profile": {
            "name": profile.name,
            "contact": "  ·  ".join(contact_bits),
            "links": list(profile.links or []),
            # Champs SÉPARÉS, pour les templates qui composent l'en-tête eux-mêmes
            # (basic). Ajout pur : classic/compact ne lisent que contact + links.
            "email": profile.email,
            "phone": profile.phone or "",
            "location": profile.location or "",
            **_split_links(list(profile.links or [])),
            # Photo : NOM DE FICHIER que le template lira à côté de render_input.json,
            # dérivé de l'empreinte de l'image. Absente quand le compte n'a pas de photo,
            # que le template n'en affiche pas, ou que l'empreinte du profil ne correspond
            # plus à l'image stockée — les templates lisent cette clé avec un défaut.
            **({"photo": photo_filename(photo_sha256)} if photo_sha256 else {}),
        },
        "education": education,
        # Langues : EN DUR (base). Hard skills / interests : SÉLECTIONNÉS (CV).
        "languages": languages,
        "hard_skills": _truncate_skills_line(
            list(cv.hard_skills), tmpl_profile.max_skills_line_chars
        ),
        "interests": _truncate_skills_line(
            list(cv.interests), tmpl_profile.max_skills_line_chars
        ),
        "experiences": experiences,
        # Soft skills : tronqués à LEUR propre limite ; affichés seulement si show_soft_skills.
        "soft_skills": _truncate_skills_line(
            list(cv.soft_skills), tmpl_profile.max_soft_skills_line_chars
        ),
        # Options d'affichage + accroche résolue (chaîne vide si désactivée), lues par le .typ.
        "show_soft_skills": tmpl_profile.show_soft_skills,
        "show_accroche": tmpl_profile.show_accroche,
        "accroche": _resolve_accroche(base, tmpl_profile, cv.focus),
        # Options RÉSOLUES du template (défauts compris). Les options de mise en page sont
        # déjà traduites dans les show_* ci-dessus ; un template y lit ses options
        # visuelles (ex. couleur de basic). Les templates sans option l'ignorent.
        "options": dict(options or {}),
    }


def build_render_input(
    base: ExperienceBase,
    cv: TailoredCV,
    tmpl_profile: TemplateProfile | None = None,
    options: dict[str, OptionValue] | None = None,
    photo_sha256: str | None = None,
) -> dict:
    """Données de rendu d'un CV (façade publique de `_build_render_input`).

    Exposée pour la retouche manuelle : le brouillon éditable est exactement ce dict, ce
    qui garantit qu'un document recompilé après édition part du MÊME état que le rendu
    d'origine (aucun second chemin d'assemblage à maintenir).
    """
    return _build_render_input(base, cv, tmpl_profile, options, photo_sha256)


def resolve_template(template: str) -> Path:
    """Chemin du fichier .typc/.typ d'un template (façade publique de `_resolve_template`)."""
    return _resolve_template(template)


def _template_options(template: str) -> dict[str, OptionValue]:
    """Options résolues d'un nom / alias / `nom?options` ; vides pour un chemin .typ."""
    try:
        return parse_template_ref(template).options
    except TemplateOptionError:
        return {}


def _resolve_template(template: str) -> Path:
    """Résout un nom court, un alias, une référence `nom?options` OU un chemin .typ."""
    if template in _TEMPLATES:
        return TEMPLATES_DIR / _TEMPLATES[template]
    try:
        return TEMPLATES_DIR / _TEMPLATES[parse_template_ref(template).name]
    except TemplateOptionError:
        pass
    path = Path(template)
    if not path.is_absolute():
        # Chemin .typ fourni par l'appelant : résolu relativement au répertoire de
        # lancement (cwd), comme les autres chemins de l'outil.
        path = Path.cwd() / path
    if path.suffix != ".typ":
        raise RenderError(
            f"template inconnu : {template!r} "
            f"(attendu : {' | '.join(_TEMPLATES)} ou un chemin .typ)"
        )
    return path


def render_cv(
    base: ExperienceBase,
    cv: TailoredCV,
    out_path: str,
    template: str = DEFAULT_TEMPLATE,
    photo: tuple[str, bytes] | None = None,
) -> str:
    """Rend le CV en PDF à out_path avec le template choisi ; retourne le chemin.

    `photo` = (empreinte SHA-256, JPEG normalisé), ou None quand il n'y a pas de photo à
    afficher. Avec une photo, la compilation passe par un dossier de travail PRIVÉ : le
    chemin ordinaire écrit dans le dossier des templates installé, qui est partagé par
    tout le process — y déposer l'image d'un compte les ferait s'accumuler et casserait
    les rendus concurrents.
    """
    template_path = _resolve_template(template)
    tmpl_profile = get_profile(template)
    sha256 = photo[0] if photo else None
    data = _build_render_input(
        base, cv, tmpl_profile, _template_options(template), sha256
    )
    if photo is None:
        return render_typst(template_path, data, str(out_path))
    work_dir = Path(out_path).parent / f".cv_work_{Path(out_path).stem}"
    return render_typst_isolated(
        template_path,
        data,
        str(out_path),
        work_dir,
        extra_files={photo_filename(photo[0]): photo[1]},
    )
