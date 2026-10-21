// Template Typst « cobalt » : RECOPIE ADAPTÉE du modèle @preview/cobalt-cv:0.1.0
// (Vikram Saraph, licence MIT — la mention de copyright ci-dessous doit rester).
//
// cobalt-cv n'est PAS une bibliothèque : son point d'entrée (template/main.typ) EST un CV
// d'exemple. `#import "@preview/cobalt-cv:0.1.0"` n'apporte que des fonctions liées à SA
// configuration figée (couleur #002366, polices Noto) et aucune de ses règles `set`/`show`
// (elles restent locales au module). L'usage prévu est `typst init` puis édition du
// fichier : on fait donc de même, en figeant ici la version d'origine. Seule dépendance
// restante : @preview/fontawesome:0.6.0 (polices Font Awesome 7 à fournir).
//
// Mise en page d'origine conservée : nom centré (serif, capitales, accent), ligne de
// contacts avec icônes, résumé entre deux filets, puis deux colonnes (barre latérale
// ombrée à gauche : formation et compétences ; colonne principale : expériences).
// Mêmes données que cv_basic.typ (render_input.json, mêmes clés) et mêmes zones
// éditables (field).
//
// Écarts assumés par rapport au modèle :
//  - contacts : liste construite depuis les données (e-mail, téléphone, ville, GitHub,
//    LinkedIn, site), boîtes insécables à 9 pt au lieu d'une grille de 4 colonnes égales
//    à 10 pt (l'e-mail et le lien LinkedIn ne tiennent pas dans un quart de page) ;
//  - police du corps nommée (Libertinus Serif, défaut implicite de Typst dans le modèle) ;
//  - `education()` : pas de lieu séparé dans les données (il est dans `school`) ;
//  - `experience()` : intitulé en titre coloré, organisation en italique à gauche, dates
//    seules à droite (pas de lieu séparé) ; sert aussi aux projets ;
//  - sections ajoutées dans le style de la barre latérale (`== rubrique` + valeurs
//    séparées par « | ») : langues, savoir-être, centres d'intérêt.
//
// ---------------------------------------------------------------------------------
// MIT License — Copyright (c) Vikram Saraph (cobalt-cv). Permission is hereby granted,
// free of charge, to any person obtaining a copy of this software... (texte complet :
// LICENSE du package @preview/cobalt-cv:0.1.0, à copier à côté de ce fichier).
// ---------------------------------------------------------------------------------

#import "@preview/fontawesome:0.6.0": fa-icon

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ─── Configuration (bloc d'origine du modèle, piloté par les options) ────────────
#let options = data.at("options", default: (:))
// Option VISUELLE « couleur » : accent (nom, titres, filets, icônes). cobalt = défaut du modèle.
#let accent_colors = (
  cobalt: rgb("#002366"),
  bordeaux: rgb("#7A1F3D"),
  vert: rgb("#1D5C4D"),
  gris: rgb("#333333"),
)
// Option VISUELLE « fond_barre » : fond de la barre latérale.
#let sidebar_fills = (gris-bleu: rgb("#eef0f5"), blanc: white)
// Option de MISE EN PAGE « barre » : proportions barre latérale / colonne principale.
#let col_ratios = (etroite: (2fr, 8fr), normale: (3fr, 7fr), large: (4fr, 6fr))

#let name         = data.profile.name
#let accent       = accent_colors.at(options.at("couleur", default: "cobalt"))
#let sidebar-fill = sidebar_fills.at(options.at("fond_barre", default: "gris-bleu"))
#let sans-font    = "Noto Sans"
#let serif-font   = "Noto Serif"
#let col-ratio    = col_ratios.at(options.at("barre", default: "normale"))
// ──────────────────────────────────────────────────────────────────────────────

#set document(title: [#upper(name)], author: name)
// Police du corps : le modèle n'en fixe aucune et hérite de la police PAR DÉFAUT de Typst
// (Libertinus Serif depuis 0.12). On la nomme pour ne pas dépendre d'un changement de défaut.
#set text(size: 10pt, lang: "fr", font: "Libertinus Serif")
#show heading.where(level: 1): set text(font: sans-font, tracking: 0.1em, weight: 500, fill: accent)
#show heading.where(level: 2): set text(size: 12pt)

#set page(paper: "a4", margin: (
  top: 1cm,
  left: 1cm,
  right: 1cm,
  bottom: 1cm,
))

// ─── Helper functions (modèle d'origine, adaptés aux données) ─────────────────

#let resume-title() = text(
  font: serif-font,
  tracking: 0.1em,
  weight: 500,
  size: 28pt,
  fill: accent,
)[#upper(name)]

// Puces : chacune est un champ éditable, identifié par son id de TRAÇABILITÉ.
#let experience(it) = [
  == #text(fill: accent)[#it.title]

  #grid(
    columns: (1fr, auto),
    align: (left, right),
    column-gutter: 1em,
    [ #emph[#it.organization] ], [ #emph[#it.dates] ],
  )

  #for b in it.bullets {
    [- #field(b.id, b.text)]
  }
]

#let education(e) = [
  == #e.school

  #e.dates \
  #text(weight: "bold")[#e.degree]
]

// Rubrique de la barre latérale : `== libellé` puis valeurs séparées par « | ».
#let skill-category(category, body) = [
  == #category

  #body
]

// ─── Header ───────────────────────────────────────────────────────────────────

#let profile_field(key) = data.profile.at(key, default: "")
// Liens : champs SANS schéma (cf. render.py::_split_links) -> on préfixe https://.
#let contacts = (
  ("email", "envelope", "Font Awesome 7 Free Solid", k => "mailto:" + k),
  ("phone", "phone", "Font Awesome 7 Free Solid", none),
  ("location", "location-dot", "Font Awesome 7 Free Solid", none),
  ("github", "github", "Font Awesome 7 Brands", k => "https://" + k),
  ("linkedin", "linkedin", "Font Awesome 7 Brands", k => "https://" + k),
  ("site", "globe", "Font Awesome 7 Free Solid", k => "https://" + k),
).filter(c => profile_field(c.at(0)) != "").map(((key, icon, font, target)) => {
  let value = profile_field(key)
  [#text(fill: accent)[#fa-icon(icon, font: font)] #if target == none { value } else { link(target(value), value) }]
})

#align(center)[
  #block(below: 0.9em, resume-title())
  #set text(size: 9pt)
  // Un élément = une boîte insécable : le retour à la ligne se fait ENTRE les éléments.
  #contacts.map(box).join(h(1.2em))
]

#line(length: 100%, stroke: accent)

// ── Phrase d'accroche (OPT-IN) : emplacement du résumé du modèle ─────────────────
#if data.show_accroche and data.accroche != "" [
  #field("accroche", data.accroche)

  #line(length: 100%, stroke: accent)
]

// ─── Body ─────────────────────────────────────────────────────────────────────

#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

#grid(
  columns: col-ratio,
  rows: auto,
  fill: (sidebar-fill, none),
  inset: 5pt,
  column-gutter: 0.5cm,
  [
    // ── Sidebar ──────────────────────────────────────────────────────────────
    #if data.education.len() > 0 [
      = #upper("Formation")

      #for e in data.education [
        #education(e)
      ]

      #line(stroke: (dash: "dashed", paint: accent), length: 90%)
    ]

    = #upper("Compétences")

    #if data.hard_skills.len() > 0 {
      skill-category("Techniques", field("hard_skills", data.hard_skills.join(", "),
        body: data.hard_skills.join(" | ")))
    }
    #if data.show_soft_skills and data.soft_skills.len() > 0 {
      skill-category("Savoir-être", field("soft_skills", data.soft_skills.join(", "),
        body: data.soft_skills.join(" | ")))
    }
    #if data.languages.len() > 0 {
      skill-category("Langues", data.languages.map(l => [#l.name : #l.level]).join(" | "))
    }
    #if data.interests.len() > 0 {
      skill-category("Centres d'intérêt", field("interests", data.interests.join(", "),
        body: data.interests.join(" | ")))
    }
    // [mesure:fin-barre]
  ],
  [
    // ── Main content ─────────────────────────────────────────────────────────
    #if experiences.len() > 0 [
      = #upper("Expérience professionnelle")

      #for it in experiences [
        #experience(it)
      ]
    ]

    #if projets.len() > 0 [
      = #upper("Projets")

      #for it in projets [
        #experience(it)
      ]
    ]
    // [mesure:fin-principale]
  ],
)

#line(length: 100%, stroke: accent)
