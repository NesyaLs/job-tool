"""Export des offres en fichiers .txt — POINT DE JONCTION avec le pipeline existant.

Le générateur (CV/lettre) consomme une offre comme un FICHIER TEXTE BRUT
(ex. `python3 scripts/run_cover_letter.py --offre <fichier.txt>`). Ici on écrit UN .txt
par offre, en texte lisible : en-tête court (titre/entreprise/lieu/contrat/télétravail/url)
puis la description. On N'APPELLE PAS le générateur — on produit juste les fichiers.
"""

from __future__ import annotations

import re
from pathlib import Path

from jobtool.aggregator.models import Job
from jobtool.aggregator.normalize import normalize_text


def slugify(value: str, max_len: int = 60) -> str:
    """Slug sûr pour un nom de fichier (minuscules, tirets, sans accents)."""
    base = normalize_text(value)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:max_len].strip("-") or "offre"


def job_to_offer_text(job: Job) -> str:
    """Rend une offre en texte brut cohérent avec les .txt consommés par le pipeline."""
    lines: list[str] = [job.title.strip() or "(sans titre)"]
    sous_titre = " — ".join(p for p in (job.company.strip(), job.location.strip()) if p)
    if sous_titre:
        lines.append(sous_titre)
    if job.contract_type.strip():
        lines.append(f"Contrat : {job.contract_type.strip()}")
    if job.remote and job.remote != "inconnu":
        lines.append(f"Télétravail : {job.remote}")
    if job.salary and (job.salary.min or job.salary.max):
        borne = "-".join(str(v) for v in (job.salary.min, job.salary.max) if v)
        devise = job.salary.currency or ""
        lines.append(f"Rémunération : {borne} {devise}".strip())
    if job.url.strip():
        lines.append(f"Source : {job.url.strip()}")
    lines.append("")  # ligne vide avant le corps
    lines.append(job.description.strip())
    return "\n".join(lines).rstrip() + "\n"


def export_jobs(jobs: list[Job], out_dir: str | Path) -> list[Path]:
    """Écrit un .txt par offre DISTINCTE (dédupliquée par dedup_hash) sous out_dir.

    Nom de fichier : `<slug entreprise-titre>__<dedup_hash>.txt` (le hash garantit l'unicité
    et évite toute collision). Retourne la liste des chemins écrits.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    seen: set[str] = set()
    for job in jobs:
        if job.dedup_hash in seen:
            continue  # une seule sortie par offre distincte
        seen.add(job.dedup_hash)
        slug = slugify(f"{job.company}-{job.title}")
        path = out / f"{slug}__{job.dedup_hash}.txt"
        path.write_text(job_to_offer_text(job), encoding="utf-8")
        written.append(path)
    return written
