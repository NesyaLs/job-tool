"""Génère le référentiel géographique français embarqué dans le paquet.

Source : l'API Découpage administratif de l'État (https://geo.api.gouv.fr), données
publiques. Le résultat est VERSIONNÉ dans le dépôt : la normalisation des lieux ne fait
jamais d'appel réseau, et deux exécutions donnent le même résultat tant que ce fichier
ne change pas. Relancer ce script seulement pour rafraîchir le référentiel (fusions de
communes, etc.), puis relire le diff.

Fichiers produits (src/jobtool/aggregator/geo_data/) :
  - regions.csv       code,nom
  - departements.csv  code,nom,region
  - communes.csv      nom,departement,population,codes_postaux (séparés par « ; »)

Usage : .venv/bin/python scripts/build_geo_referential.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import requests

API = "https://geo.api.gouv.fr"
OUT = (
    Path(__file__).resolve().parents[1] / "src" / "jobtool" / "aggregator" / "geo_data"
)


def _get(path: str, **params) -> list[dict]:
    resp = requests.get(f"{API}{path}", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    regions = sorted(_get("/regions"), key=lambda r: r["code"])
    departements = sorted(_get("/departements"), key=lambda d: d["code"])
    communes = _get(
        "/communes",
        fields="nom,codeDepartement,population,codesPostaux",
        format="json",
    )
    communes.sort(key=lambda c: (c["nom"], c.get("codeDepartement") or ""))

    with (OUT / "regions.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "nom"])
        w.writerows([r["code"], r["nom"]] for r in regions)
    with (OUT / "departements.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "nom", "region"])
        w.writerows([d["code"], d["nom"], d["codeRegion"]] for d in departements)
    with (OUT / "communes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["nom", "departement", "population", "codes_postaux"])
        for c in communes:
            if not c.get("codeDepartement"):
                continue
            w.writerow(
                [
                    c["nom"],
                    c["codeDepartement"],
                    c.get("population") or 0,
                    ";".join(c.get("codesPostaux") or []),
                ]
            )
    print(
        f"{len(regions)} régions, {len(departements)} départements, "
        f"{len(communes)} communes -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
