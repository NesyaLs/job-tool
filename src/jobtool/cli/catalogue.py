"""Commandes `jobtool catalogue …` : entretien et calibrage du catalogue (développement).

    jobtool catalogue doublons [--min 0.75 --max 0.90]   paires sous le seuil de fusion
    jobtool catalogue dedoublonner [--appliquer]          fusions sur l'existant
    jobtool catalogue stats                               volumes par source et niveau
    jobtool catalogue renormaliser                        recalcul des champs normalisés

Imports lourds différés : `jobtool --help` reste instantané.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Entretien du catalogue d'offres (dédoublonnage, calibrage, volumes).",
    no_args_is_help=True,
)

_DB_HELP = "fichier SQLite à utiliser à la place de la base de l'application"


def _short(offer: dict) -> str:
    where = offer.get("city") or offer.get("department") or offer.get("location") or "?"
    return f"{offer.get('title', '')[:60]} — {offer.get('company', '')[:30]} — {where}"


@app.command("doublons")
def doublons(
    low: float = typer.Option(0.75, "--min", help="score minimal affiché"),
    high: float = typer.Option(0.90, "--max", help="score maximal (exclu) = seuil"),
    limit: int = typer.Option(50, help="nombre de paires affichées"),
    db: str | None = typer.Option(None, help=_DB_HELP),
) -> None:
    """Doublons PROBABLES : paires comparables sous le seuil de fusion, les plus proches
    d'abord. À examiner pour régler le seuil (aggregator/dedup.FUZZY_THRESHOLD)."""
    from jobtool.services.catalogue_service import probable_duplicates

    pairs = probable_duplicates(db, low=low, high=high)
    print(f"{len(pairs)} paire(s) dans [{low:.2f} ; {high:.2f}[")
    for pair in pairs[:limit]:
        print(f"\n  score {pair.score:.3f}")
        print(f"    A [{pair.a['dedup_hash']}] {_short(pair.a)}")
        print(f"    B [{pair.b['dedup_hash']}] {_short(pair.b)}")


@app.command("dedoublonner")
def dedoublonner(
    appliquer: bool = typer.Option(
        False, "--appliquer", help="applique les fusions (sinon : simulation)"
    ),
    db: str | None = typer.Option(None, help=_DB_HELP),
) -> None:
    """Fusionne les doublons DÉJÀ présents (liens communs, empreinte approchée)."""
    from jobtool.services.catalogue_service import deduplicate

    merges = deduplicate(db, apply=appliquer)
    by_level: dict[str, int] = {}
    for m in merges:
        by_level[m.level] = by_level.get(m.level, 0) + 1
    verb = "appliquée(s)" if appliquer else "proposée(s) (simulation, --appliquer)"
    print(f"{len(merges)} fusion(s) {verb} : {by_level}")
    for m in merges[:30]:
        score = f" score {m.score:.3f}" if m.score is not None else ""
        print(f"  {m.drop} -> {m.keep}  [{m.level}{score}]")


@app.command("stats")
def stats(db: str | None = typer.Option(None, help=_DB_HELP)) -> None:
    """Volumes du catalogue : occurrences, offres, rattachements par niveau."""
    from jobtool.services.catalogue_service import stats as catalogue_stats

    s = catalogue_stats(db)
    print(f"Occurrences            : {s['occurrences']}  {s['occurrences_by_source']}")
    print(f"Offres (dédoublonnées) : {s['offers']}")
    print(f"Offres multi-sources   : {s['offers_with_several_sources']}")
    print(f"Rattachements (niveau) : {s['occurrences_by_level']}")
    print(f"Fusions a posteriori   : {s['merges_by_level']}")


@app.command("renormaliser")
def renormaliser(db: str | None = typer.Option(None, help=_DB_HELP)) -> None:
    """Recalcule les champs normalisés (lieu, contrat, clés) des occurrences anciennes."""
    from jobtool.services.catalogue_service import renormalize

    print(f"{renormalize(db)} occurrence(s) recalculée(s)")
