"""Invocation générique du binaire Typst + gestion de render_input.json.

Partie TRANSVERSE du rendu : prend un template `.typ` et un dict de données DÉJÀ
assemblé, écrit render_input.json À CÔTÉ du template (pour que le json() du
template, à chemin relatif, le trouve), lance `typst compile`, et gère proprement
binaire absent / échec de compilation. L'assemblage des données est spécifique à
chaque module (ex. cv/render.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

# Polices EMBARQUÉES (données de paquet, résolues via importlib.resources comme les
# templates). Les templates de CV récents exigent des polices absentes d'une installation
# standard (Font Awesome 6 ET 7, Inter, Fira Sans, Source Sans 3, Roboto, Noto) : sans
# elles, les icônes disparaissent et Typst se rabat en silence sur une autre famille.
FONTS_DIR = Path(str(files("jobtool.core.rendering").joinpath("fonts")))

# `--ignore-system-fonts` : le rendu ne doit dépendre QUE des polices embarquées et de
# celles du binaire Typst (New Computer Modern, Libertinus Serif…). Sans ce drapeau, une
# police installée sur le poste — souvent une autre version — se substituerait aux nôtres
# et déplacerait les coupures de ligne, donc la tenue sur une page mesurée par fill.py.
# Vérifié : les rendus de classic, compact, basic et des deux lettres sont identiques au
# pixel avec et sans ce drapeau (ils n'utilisent que des polices du binaire).
_FONT_ARGS = ["--font-path", str(FONTS_DIR), "--ignore-system-fonts"]

_INSTALL_HINT = (
    "binaire `typst` introuvable. Installe-le (au choix) :\n"
    "  - cargo install typst-cli\n"
    "  - ou télécharge la release : https://github.com/typst/typst/releases\n"
    "  - ou via un gestionnaire de paquets (ex: `snap install typst`).\n"
    "Puis vérifie avec : typst --version"
)


class RenderError(RuntimeError):
    """Erreur de rendu lisible (binaire absent, échec de compilation, etc.)."""


def render_typst(template_path: Path, data: dict, out_path: str) -> str:
    """Écrit `data` dans render_input.json à côté du template, compile, retourne out_path."""
    if shutil.which("typst") is None:
        raise RenderError(_INSTALL_HINT)

    if not template_path.exists():
        raise RenderError(f"template introuvable : {template_path}")

    # Le template lit json("render_input.json") relativement à SON dossier :
    # on écrit donc le JSON à côté du template choisi.
    render_input = template_path.parent / "render_input.json"
    render_input.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    out = Path(out_path)
    try:
        result = subprocess.run(
            ["typst", "compile", *_FONT_ARGS, str(template_path), str(out)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RenderError(_INSTALL_HINT)

    if result.returncode != 0:
        raise RenderError(
            "échec de compilation Typst :\n"
            + (result.stderr.strip() or "(pas de détail)")
        )

    return str(out)


# --- Rendu ISOLÉ + relevé des champs éditables ------------------------------
#
# `render_typst` ci-dessus écrit render_input.json À CÔTÉ du template installé : c'est
# un fichier PARTAGÉ par tout le process (deux rendus concurrents s'écrasent). Tant que
# le rendu était one-shot (une génération, un PDF), le risque restait théorique. La
# retouche manuelle recompile à chaque enregistrement : on isole donc chaque rendu dans
# son propre dossier de travail (copie des .typ + son render_input.json), ce qui rend
# les recompilations indépendantes les unes des autres. `render_typst` n'est pas touché
# (comportement préservé pour la CLI et les tests existants).


@dataclass(frozen=True)
class FieldBox:
    """Une zone éditable telle que MESURÉE par le compilateur Typst.

    Coordonnées en points PDF, origine au coin haut-gauche de la page. `text` est le
    texte effectivement rendu (déjà composé par le template) : le code Python n'a
    donc jamais à reconstituer ce qui est écrit dans le PDF.
    """

    id: str
    text: str
    page: int
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class RenderArtifacts:
    """Sorties d'un rendu isolé : le PDF, ses aperçus PNG et les boîtes des champs."""

    pdf_path: str
    fields: list[FieldBox]
    previews: list[str]  # un PNG par page, dans l'ordre
    page_width_pt: float
    page_height_pt: float
    # Défauts de mise en page que seul le compilateur peut mesurer (`<jtwarning>`), ex. un
    # titre qui passe à la ligne dans un en-tête de hauteur fixe. Signalés, jamais corrigés.
    warnings: list[str] = field(default_factory=list)


def _run_typst(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Lance le binaire typst avec `args`, en traduisant l'absence de binaire.

    Les polices embarquées sont imposées ICI, sur le sous-commande (`compile`, `eval`) :
    un appelant ne peut pas les oublier, et `eval` voit donc les mêmes polices que
    `compile` — sinon les boîtes relevées par `query_render_marks` décriraient une mise
    en page différente de celle du PDF.
    """
    if shutil.which("typst") is None:
        raise RenderError(_INSTALL_HINT)
    subcommand, *rest = args
    try:
        return subprocess.run(
            ["typst", subcommand, *_FONT_ARGS, *rest], capture_output=True, text=True
        )
    except FileNotFoundError:
        raise RenderError(_INSTALL_HINT)


def prepare_work_dir(
    template_path: Path,
    data: dict,
    work_dir: Path,
    *,
    extra_files: Mapping[str, bytes] | None = None,
) -> Path:
    """Prépare un dossier de compilation autonome ; retourne le template à compiler.

    Copie TOUS les `.typ` du dossier du template (le template lui-même et ses voisins
    importés, dont `_edit.typ`) puis écrit `render_input.json` à côté : la compilation
    ne dépend alors plus de l'arborescence installée et n'écrase aucun fichier partagé.

    `extra_files` ({nom de fichier: octets}) dépose des fichiers supplémentaires que le
    template lit lui-même — aujourd'hui la photo de profil. Ils vont ICI et jamais dans le
    dossier des templates installé, qui est partagé par tout le process : y écrire la
    donnée d'un compte la ferait fuir vers le rendu concurrent d'un autre. Les noms sont
    traités comme de simples noms de fichier, jamais comme des chemins.
    """
    if not template_path.exists():
        raise RenderError(f"template introuvable : {template_path}")

    work_dir.mkdir(parents=True, exist_ok=True)
    for typ in template_path.parent.glob("*.typ"):
        shutil.copy2(typ, work_dir / typ.name)

    for name, content in (extra_files or {}).items():
        if Path(name).name != name:
            raise RenderError(f"nom de fichier joint invalide : {name!r}")
        (work_dir / name).write_bytes(content)

    (work_dir / "render_input.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return work_dir / template_path.name


def render_typst_isolated(
    template_path: Path,
    data: dict,
    out_path: str,
    work_dir: Path,
    *,
    extra_files: Mapping[str, bytes] | None = None,
) -> str:
    """Compile dans un dossier de travail PRIVÉ ; retourne out_path.

    Même contrat que `render_typst`, mais sans écrire dans le dossier des templates
    installé. C'est le chemin obligatoire dès qu'un fichier propre à un compte (photo)
    accompagne la compilation.
    """
    work_template = prepare_work_dir(
        template_path, data, work_dir, extra_files=extra_files
    )
    result = _run_typst(["compile", str(work_template), str(out_path)])
    if result.returncode != 0:
        raise RenderError(
            "échec de compilation Typst :\n"
            + (result.stderr.strip() or "(pas de détail)")
        )
    return str(out_path)


def query_render_marks(template_path: Path) -> tuple[list[FieldBox], list[str]]:
    """Relève, en UNE évaluation, les zones éditables (`<jtfield>`) et les avertissements
    de mise en page (`<jtwarning>`) d'un template déjà prêt à compiler.

    Le compilateur est la SEULE source de vérité : on ne devine rien depuis le PDF
    (pas d'appariement de texte sur la couche d'extraction), on lit ce que Typst a
    mesuré pendant la mise en page.
    """
    result = _run_typst(
        [
            "eval",
            "(fields: query(<jtfield>).map(it => it.value),"
            " warnings: query(<jtwarning>).map(it => it.value))",
            "--in",
            str(template_path),
            "--format",
            "json",
        ]
    )
    if result.returncode != 0:
        raise RenderError(
            "échec du relevé des zones éditables :\n"
            + (result.stderr.strip() or "(pas de détail)")
        )

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError(f"relevé des zones éditables illisible : {exc}")

    boxes = [
        FieldBox(
            id=str(item["id"]),
            text=str(item["text"]),
            page=int(item["page"]),
            x=float(item["x"]),
            y=float(item["y"]),
            w=float(item["w"]),
            h=float(item["h"]),
        )
        for item in raw["fields"]
    ]
    return boxes, [str(message) for message in raw["warnings"]]


def query_field_boxes(template_path: Path) -> list[FieldBox]:
    """Zones éditables (`<jtfield>`) d'un template déjà prêt à compiler."""
    return query_render_marks(template_path)[0]


def render_with_fields(
    template_path: Path,
    data: dict,
    work_dir: Path,
    *,
    preview_ppi: int = 150,
) -> RenderArtifacts:
    """Compile un template dans son propre dossier : PDF + aperçus PNG + boîtes des champs.

    Tout est produit depuis le MÊME état source (`data`), en une passe : le PDF téléchargé,
    l'image affichée et les coordonnées des zones cliquables ne peuvent pas diverger.
    """
    from jobtool.core.rendering.pdf_checks import AtsCheckError, page_size_pt

    work_template = prepare_work_dir(template_path, data, work_dir)

    pdf_path = work_dir / "document.pdf"
    compiled = _run_typst(["compile", str(work_template), str(pdf_path)])
    if compiled.returncode != 0:
        raise RenderError(
            "échec de compilation Typst :\n"
            + (compiled.stderr.strip() or "(pas de détail)")
        )

    # Aperçus : un PNG par page. Typst exige le motif `{n}` dès qu'il peut y avoir
    # plusieurs pages — on garde donc le motif même quand le document en fait une.
    for stale in work_dir.glob("preview-*.png"):
        stale.unlink()
    preview_pattern = work_dir / "preview-{n}.png"
    previewed = _run_typst(
        ["compile", str(work_template), str(preview_pattern), "--ppi", str(preview_ppi)]
    )
    if previewed.returncode != 0:
        raise RenderError(
            "échec du rendu de l'aperçu :\n"
            + (previewed.stderr.strip() or "(pas de détail)")
        )
    previews = sorted(
        (str(p) for p in work_dir.glob("preview-*.png")),
        key=lambda p: int(Path(p).stem.split("-")[-1]),
    )

    try:
        width_pt, height_pt = page_size_pt(str(pdf_path))
    except AtsCheckError as exc:  # pdfinfo absent : erreur de rendu lisible
        raise RenderError(str(exc))

    fields, warnings = query_render_marks(work_template)
    return RenderArtifacts(
        pdf_path=str(pdf_path),
        fields=fields,
        warnings=warnings,
        previews=previews,
        page_width_pt=width_pt,
        page_height_pt=height_pt,
    )
