"""Nettoyage HTML -> texte brut, via la STDLIB uniquement (html.parser), sans dépendance.

SmartRecruiters renvoie la description (`jobAd.sections[].text`) en HTML BRUT (<p>, <ul>, <li>…).
Le schéma normalisé du projet attend du texte. On convertit donc en texte lisible : les blocs
(<p>, <li>, <br>, titres…) deviennent des sauts de ligne, les <li> sont préfixés d'une puce,
les entités (&amp;, &eacute;…) sont décodées (convert_charrefs), et les espaces sont normalisés.

DÉFENSIF : entrée None/vide -> "" ; HTML malformé -> on renvoie le meilleur effort sans lever
(html.parser est tolérant ; un filet ultime capture l'imprévu).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Balises de niveau bloc : leur ouverture/fermeture introduit un saut de ligne logique.
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "ul",
    "ol",
    "li",
    "tr",
    "section",
    "article",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
}


class _TextExtractor(HTMLParser):
    """Accumule le texte ; insère des sauts de ligne aux frontières de blocs, une puce par <li>."""

    def __init__(self) -> None:
        # convert_charrefs=True : les entités HTML sont décodées en texte automatiquement.
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        # <br/> et autres balises auto-fermantes.
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str | None) -> str:
    """Convertit du HTML en texte brut lisible. None/vide -> "" ; ne lève jamais."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        raw = parser.text()
    except Exception:  # noqa: BLE001 — meilleur effort : HTML imprévu ne doit jamais planter
        raw = re.sub(r"<[^>]+>", " ", html)  # repli grossier : on retire les balises
    # Normalisation : espaces/tabs compactés par ligne, lignes vides supprimées, trim global.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)
