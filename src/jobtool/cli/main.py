"""Point d'entrée CLI de jobtool (Typer) : `jobtool <commande>`.

Commandes : `jobtool {aggregate|batch|cv|letter|api|catalogue}`. Les commandes gardent leurs imports lourds
différés en interne, pour que `jobtool --help` réponde sans clé ni dépendance réseau.
"""

import typer

from jobtool.cli.aggregate import aggregate
from jobtool.cli.api import api
from jobtool.cli.batch import batch
from jobtool.cli.catalogue import app as catalogue_app
from jobtool.cli.cv import cv
from jobtool.cli.letter import letter

app = typer.Typer(
    help="jobtool — agrégateur d'offres + génération CV/lettre.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """jobtool — agrégateur d'offres + génération CV/lettre.

    Callback (sans logique) : force Typer à exposer des SOUS-COMMANDES nommées
    (`jobtool aggregate …`) au lieu de réduire l'app à sa commande unique.
    """


app.command()(aggregate)
app.command()(batch)
app.command()(cv)
app.command()(letter)
app.command()(api)
app.add_typer(catalogue_app, name="catalogue")


if __name__ == "__main__":
    app()
