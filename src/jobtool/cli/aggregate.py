"""Commande `jobtool aggregate` : COLLECTE des offres -> catalogue commun (+ .txt).

Reproduit exactement l'ancien scripts/run_aggregator.py : mêmes options, mêmes défauts à
None qui retombent sur AggregatorSettings, même logique d'isolation des sources (une source
sans identifiants est signalée mais n'empêche pas l'autre). Les imports lourds (connecteurs,
pipeline, settings -> requests) sont DIFFÉRÉS dans la fonction pour que `jobtool --help` et
`jobtool aggregate --help` répondent sans clé ni dépendance réseau.
"""

from enum import Enum

import typer


class Source(str, Enum):
    """Source(s) interrogeable(s). `both` (rétrocompat) = FT + Adzuna ; `all` = toutes."""

    france_travail = "france_travail"
    adzuna = "adzuna"
    smartrecruiters = "smartrecruiters"
    workday = "workday"
    both = "both"
    all = "all"


def _resolve_sources(selected: list["Source"]) -> list[str]:
    """Déplie `both`/`all` en noms concrets de sources, dédupliqués (ordre préservé)."""
    from jobtool.aggregator.registry import ALL_SOURCES

    expanded: list[str] = []
    for s in selected:
        if s is Source.all:
            expanded.extend(ALL_SOURCES)
        elif s is Source.both:
            expanded.extend(["france_travail", "adzuna"])
        else:
            expanded.append(s.value)

    seen: set[str] = set()
    ordered: list[str] = []
    for name in expanded:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def aggregate(
    config: str | None = typer.Option(
        None, help="fichier de critères (défaut : data/aggregator/config.yaml)"
    ),
    db: str | None = typer.Option(
        None,
        help="fichier SQLite à utiliser À LA PLACE de la base de l'application (défaut : "
        "la même que l'API — Postgres si DATABASE_URL est définie, sinon "
        "data/aggregator/jobs.db)",
    ),
    out: str | None = typer.Option(
        None, help="dossier d'export des .txt (défaut : output/aggregator/offres/)"
    ),
    max_results: int | None = typer.Option(
        None, help="surcharge le max_results de la config (nb d'offres max à collecter)"
    ),
    keyword: str | None = typer.Option(
        None,
        help="surcharge le mot-clé de recherche (motsCles) de la config pour cette run "
        "(ex. --keyword 'ingénieur réseau'). Absent : on garde la valeur du YAML.",
    ),
    location: str | None = typer.Option(
        None,
        help="surcharge la localisation = département(s) INSEE (criteria.departement), "
        "comme le YAML (ex. --location '75,92,93,94'). N'affecte QUE les sources qui "
        "filtrent par géo (France Travail au premier chef ; Adzuna est national et "
        "l'ignore ; SmartRecruiters/Workday ont leur géo propre dans leur config). "
        "Absent : on garde la valeur du YAML.",
    ),
    verbose: bool = typer.Option(
        False,
        help="affiche le DÉTAIL COMPLET des erreurs HTTP (corps de réponse non tronqué, "
        "URL, params). Activable aussi via JOBTOOL_AGG_DEBUG=1.",
    ),
    source: list[Source] = typer.Option(
        [],
        help="source(s) à interroger (répétable ; défaut : celles ACTIVÉES dans la "
        "section `sources:` du config.yaml). Valeurs : france_travail, adzuna, "
        "smartrecruiters, workday, both (=FT+Adzuna), all. Un échec d'une source "
        "n'interrompt pas les autres.",
    ),
) -> None:
    """Collecte les offres de toutes les sources activées et alimente le catalogue."""
    # Imports lourds (service -> connecteurs/pipeline -> requests) déférés : `--help` ne dépend
    # de rien. Toute l'orchestration vit dans le service ; la CLI ne fait QUE de la présentation.
    from jobtool.aggregator.config import ConfigError
    from jobtool.services.aggregation_service import CollectionBusy, run_aggregation

    def progress(src) -> None:
        # Appelé depuis le fil de chaque source : une ligne par source terminée.
        if src.status in ("done", "failed"):
            mark = "✗" if src.status == "failed" else ("⚠" if src.errors else "✓")
            print(f"  {mark} {src.source} : {src.fetched} offre(s)", flush=True)

    source_names = _resolve_sources(source) if source else None
    print("Collecte en cours (sources interrogées en parallèle)…", flush=True)
    try:
        # La collecte alimente le catalogue COMMUN : elle ne dépend d'aucun compte.
        report = run_aggregation(
            sources=source_names,
            keyword=keyword,
            location=location,
            max_results=max_results,
            config_path=config,
            db=db,
            export_dir=out,
            debug=verbose,  # --verbose ; le service y ajoute JOBTOOL_AGG_DEBUG
            on_progress=progress,
        )
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except CollectionBusy as exc:
        typer.echo(
            f"Erreur : une collecte est déjà en cours ({exc.run_id}) — attends qu'elle "
            "se termine.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Affichage de l'en-tête, reconstruit depuis les critères EFFECTIFS renvoyés par le service.
    print(f"Config : {report.config_path}")
    print(f"Source(s) : {', '.join(report.sources)}")
    for i, criteria in enumerate(report.searches, 1):
        geo, geo_warning = criteria.resolve_geo()
        print(
            f"Recherche {i} : motsCles={criteria.mots_cles!r} "
            f"max_results={criteria.max_results} "
            f"géo France Travail={geo or '(national, aucun filtre)'}"
        )
        if geo_warning:
            print(f"⚠ {geo_warning}")
    print(
        "           (Adzuna : national ; SmartRecruiters : par entreprise ; "
        "Workday : par site carrière)"
    )

    if report.usable_sources == 0:
        raise SystemExit(
            "Erreur : aucune source utilisable.\n"
            + "\n".join(report.summary.errors)
            + "\nRenseigne les identifiants dans .env (ou la section smartrecruiters du config)."
        )

    summary = report.summary
    print("\n=== Résumé de la run ===")
    for name, src in summary.per_source.items():
        flag = "✗" if src.status == "failed" else ("⚠" if src.errors else "✓")
        print(
            f"  {flag} {name:<16} {src.fetched:>5} offre(s)"
            + (f", {len(src.errors)} erreur(s)" if src.errors else "")
        )
    levels = summary.by_level
    print(f"Offres récupérées     : {summary.fetched}")
    print(f"Offres distinctes     : {summary.distinct}  (après dédoublonnage)")
    print(f"  - nouvelles         : {summary.new}")
    print(f"  - déjà connues      : {summary.already_known}")
    print(
        "Rattachements         : "
        f"identifiant {levels.get('identifiant', 0)}, "
        f"lien {levels.get('url', 0)}, "
        f"empreinte {levels.get('approchee', 0)}, "
        f"nouvelles {levels.get('nouvelle', 0)}"
    )
    if report.purged or report.kept_expired:
        print(
            f"Offres expirées       : {report.purged} retirée(s), "
            f"{report.kept_expired} conservée(s) (favori, candidature…)"
        )
    print(f"Fichiers .txt écrits  : {summary.exported}  (dans {report.export_dir})")
    print(f"Base                  : {report.db_path}")
    if summary.errors:
        print(f"\n⚠ {len(summary.errors)} erreur(s) NON fatale(s) (run poursuivie) :")
        for err in summary.errors:
            print(f"  - {err}")
        raise typer.Exit(code=1)
    print("\nOK : run terminée sans erreur.")
