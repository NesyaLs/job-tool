"""Preuve du nettoyage HTML -> texte (stdlib) : blocs, listes, entités, défensif."""

from __future__ import annotations

from jobtool.aggregator.html_clean import html_to_text


def test_empty_and_none_give_empty_string():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_paragraphs_and_breaks_become_newlines():
    html = "<p>Premier paragraphe.</p><p>Deuxième<br/>avec saut.</p>"
    assert html_to_text(html) == "Premier paragraphe.\nDeuxième\navec saut."


def test_list_items_get_bullets():
    html = "<ul><li>Python</li><li>SQL</li></ul>"
    assert html_to_text(html) == "- Python\n- SQL"


def test_entities_are_decoded():
    assert html_to_text("<p>R&amp;D &eacute;quipe</p>") == "R&D équipe"


def test_whitespace_is_collapsed():
    html = "<p>  trop    d'espaces\t et\n\n lignes vides </p>"
    assert html_to_text(html) == "trop d'espaces et\nlignes vides"


def test_malformed_html_does_not_raise():
    # Pas d'exception, meilleur effort : on récupère au moins le texte visible.
    out = html_to_text("<p>texte <b>gras sans fermeture")
    assert "texte" in out and "gras sans fermeture" in out
