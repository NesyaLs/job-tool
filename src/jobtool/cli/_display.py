"""Blocs d'affichage partagés par les commandes CLI.

Présentation UNIQUEMENT : ces fonctions impriment, elles ne mesurent ni ne décident rien.
Les contrôles vivent dans `core/rendering/pdf_checks` et leurs résultats arrivent ici tout
faits.

Pourquoi un module : `jobtool cv` et `jobtool letter` affichent le même couple de blocs
(pagination puis extractibilité ATS) à partir du même triplet `(pages, page_violations,
missing)`. Écrit deux fois, il divergerait comme a divergé l'enchaînement de la lettre.
Seule la phrase de succès ATS diffère d'une commande à l'autre — elle est donc un paramètre,
pas une copie.
"""

from __future__ import annotations


def print_check_blocks(
    pages: int,
    page_violations: list[str],
    missing: list[str],
    *,
    ats_ok: str,
) -> None:
    """États pagination + ATS, exactement comme l'historique run_pipeline.

    `ats_ok` est la phrase affichée quand rien ne manque : le CV parle de ses bullets, la
    lettre de son en-tête, et aucune des deux n'a à connaître la formulation de l'autre.
    """
    if page_violations:
        print(f"Pages : {pages} —", "; ".join(page_violations))
    else:
        print(f"Pages : {pages} — OK (tient sur une seule page).")

    if not missing:
        print(ats_ok)
    else:
        print(
            f"Alerte ATS : {len(missing)} fragment(s) NON retrouvé(s) dans l'extraction :"
        )
        for fragment in missing:
            print(f"  - {fragment!r}")
