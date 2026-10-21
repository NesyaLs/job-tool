"""Modèle de RENDU d'une lettre de motivation : ce que le template consomme.

CE N'EST PAS un schéma de sortie LLM (ça viendra aux étapes 2-3). Ce modèle décrit
UNIQUEMENT les données prêtes-à-rendre injectées dans cover_letter/templates/lettre.typ.

Structure française classique « Vous / Moi / Nous » + formule. Deux blocs conditionnels
INDÉPENDANTS (même principe que les 2 options du CV) :
  - show_interet / insert_interet : insert ajouté à la partie « Vous » ;
  - show_projet  / insert_projet  : insert ajouté à la partie « Moi ».

Les inserts seront remplis aux étapes ultérieures (sélection / LLM). En mode GÉNÉRIQUE,
show_interet et show_projet valent False et les inserts sont ignorés au rendu.

L'en-tête est de la DONNÉE PURE (jamais générée) et ne référence JAMAIS le poste.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CoverLetterRoutingOutput(BaseModel):
    """Sortie structurée de la PASSE 1 (routage + extraction + sélection d'ids tracés).

    Schéma de sortie LLM, DISTINCT du modèle de rendu CoverLetterRender. La passe 1 ne
    rédige aucun texte de lettre : elle décide seulement.
      - entreprise / domaine : extraits de l'OFFRE (données d'offre, pas des faits du
        profil) -> soumis à l'hygiène de format uniquement, JAMAIS à la traçabilité.
      - mode : « cible » s'il existe du matériel pertinent tracé (projet et/ou intérêt lié
        à l'offre), « generique » sinon.
      - projets_ids : source_id d'achievements de PROJET (mêmes ids que le CV) justifiant
        un insert « projet ». Vide en générique.
      - interets_ids : intitulés EXACTS de centres d'intérêt de la base (l'intitulé est
        l'id). Vide en générique.
    Les bornes dures (traçabilité, cohérence du mode, format) sont vérifiées par
    cover_letter/routing.py, pas ici.
    """

    entreprise: str
    domaine: str
    mode: Literal["generique", "cible"]
    projets_ids: list[str] = []
    interets_ids: list[str] = []


class CoverLetterDraftOutput(BaseModel):
    """Sortie structurée de la PASSE 2 (rédaction des paragraphes), DISTINCTE du rendu.

    La passe 2 REÇOIT la décision de la passe 1 (CoverLetterRoutingOutput) et rédige le
    TEXTE des trois temps « Vous / Moi / Nous » + la formule, plus deux inserts optionnels.
    Elle ne rend AUCUN PDF (étape 4) et ne refait PAS le routage (passes étanches).

    Natures de contenu (cadrées par le prompt, vérifiées par cover_letter/draft_checks.py) :
      - paragraphe_vous : peut contenir UNE phrase libre sur l'entreprise/le domaine extraits
        par la passe 1 — SEULE exception anti-fabrication, texte NON tracé, relu par l'humain.
      - insert_interet : rempli SEULEMENT en mode ciblé ET si interets_ids non vide ; sinon None.
        S'appuie sur les intitulés EXACTS de centres d'intérêt (aucun fait inventé sur le profil).
      - insert_projet : rempli SEULEMENT en mode ciblé ET si projets_ids non vide ; sinon None.
        REFORMULE 1-2 achievements RÉELS du projet désigné, sans fait neuf (chiffre/techno/résultat).
    Les bornes dures (placeholders, longueurs, cohérence mode/inserts, anti-chiffre) sont
    vérifiées par le CODE, jamais garanties par le LLM.
    """

    paragraphe_vous: str
    insert_interet: str | None = None
    paragraphe_moi: str
    insert_projet: str | None = None
    paragraphe_nous: str
    formule: str


#: Formule d'appel par défaut, lue par tous les templates de lettre (modifiable en mode pro).
DEFAULT_SALUTATION = "Madame, Monsieur,"
#: Première ligne du bloc destinataire (template « moderne ») quand l'offre n'en dit rien.
DEFAULT_DESTINATAIRE = "Service Recrutement"


class CoverLetterRender(BaseModel):
    """Données prêtes-à-rendre d'une lettre de motivation (consommées par les templates).

    Un seul modèle pour tous les templates (cf. cover_letter/registry.py) : un champ qu'un
    template n'affiche pas (ex. `destinataire` pour « sobre ») y est simplement ignoré.
    """

    # --- En-tête (données pures, jamais générées ; pas de poste) ---------------
    nom: str
    coordonnees: list[str] = []  # une ligne par entrée (email, téléphone, ville, ...)
    date: str
    lieu: str
    entreprise: str
    # Titre professionnel sous le nom (« moderne ») : absent de la base -> vide.
    titre: str = ""
    destinataire: str = DEFAULT_DESTINATAIRE
    salutation: str = DEFAULT_SALUTATION

    # Objet OPTIONNEL : ne référence PAS le poste (réglable plus tard). Vide/None = pas d'objet.
    objet: str | None = None

    # --- Corps fixe : Vous / Moi / Nous + formule ------------------------------
    paragraphe_vous: str
    paragraphe_moi: str
    paragraphe_nous: str
    formule: str

    # --- Blocs conditionnels indépendants --------------------------------------
    show_interet: bool = False
    insert_interet: str = ""  # inséré dans la partie « Vous » si show_interet

    show_projet: bool = False
    insert_projet: str = ""  # inséré dans la partie « Moi » si show_projet
