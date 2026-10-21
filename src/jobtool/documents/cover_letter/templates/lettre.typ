// Template « sobre » (historique) et paramétré d'une lettre de motivation française.
// Un SEUL fichier (pas de duplication générique/ciblé) : les variantes se font par les
// blocs conditionnels show_interet / show_projet, comme cv_compact.typ avec ses options.
// Respecte le contrat commun des templates de lettre (cf. cover_letter/registry.py).
// Données injectées via render_input.json (écrit par le renderer). AUCUN poste mentionné.
// Texte 100% sélectionnable (ATS), calibré pour tenir LARGEMENT sur 1 page.

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte et
// leur texte sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── Réglages de mise en page (sobres, lisibles) ─────────────────────────────────
#set page(margin: (x: 2.2cm, y: 2.2cm))
#set text(size: 11pt, lang: "fr")
#set par(justify: true, leading: 0.62em, spacing: 1.1em)

// ── Inserts conditionnels : concaténés au paragraphe (un espace de jonction) ────
// On construit des chaînes en mode code pour un collage propre (pas d'espace parasite).
#let insert_vous = if data.show_interet and data.insert_interet != "" {
  " " + data.insert_interet
} else { "" }
#let insert_moi = if data.show_projet and data.insert_projet != "" {
  " " + data.insert_projet
} else { "" }

// ── En-tête : expéditeur (nom + coordonnées, une ligne chacune) ─────────────────
#text(weight: "bold")[#data.nom]
#for ligne in data.coordonnees [ \ #ligne ]

// Entreprise + lieu/date, alignés à droite. (Pas de poste.)
#v(0.7em)
#align(right)[
  #data.entreprise \
  #data.lieu, le #data.date
]

#v(0.9em)

// ── Objet (optionnel) : seulement si non vide ───────────────────────────────────
#if data.objet != "" [
  #strong[Objet : #field("objet", data.objet)]
]

// Formule d'appel : une donnée (défaut « Madame, Monsieur, »), modifiable en mode pro.
#if data.salutation != "" [#data.salutation]

// ── Corps : Vous / Moi / Nous ───────────────────────────────────────────────────
// Chaque paragraphe est un champ éditable portant le texte DÉJÀ composé (insert
// conditionnel compris) : ce que l'humain retouche est exactement ce qu'il lit.
#field("paragraphe_vous", data.paragraphe_vous + insert_vous)

#field("paragraphe_moi", data.paragraphe_moi + insert_moi)

#field("paragraphe_nous", data.paragraphe_nous)

#v(0.4em)
#field("formule", data.formule)
