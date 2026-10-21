// Template Typst « lavandula » : habillage du package @preview/lavandula (version ÉPINGLÉE
// ci-dessous : la tenue sur 1 page est mesurée sur CE rendu précis). Deux colonnes : barre
// latérale colorée (nom, contacts, accroche, compétences, langues, intérêts) et colonne
// principale (expériences, projets, formation), puces à icône, pastilles de compétences.
// Mêmes données que cv_basic.typ (render_input.json, mêmes clés) et mêmes zones
// éditables (field).
//
// Choix assumés :
//  - format A4 et marges NULLES imposés par `lavandula-theme` (non paramétrables) : les
//    retraits viennent de l'`inset` de la grille de `cv` (17 pt horizontal, 25 pt vertical) ;
//  - titre de poste sous le nom (`====` dans l'exemple) : absent des données, omis ;
//  - `section-element` met le titre dans une colonne `auto` et la date dans la colonne `1fr`
//    restante : un titre trop long écrase la date. On y met donc l'INTITULÉ seul, et
//    l'organisation en italique au début du corps. Formation : titre = ÉCOLE (courte),
//    diplôme en italique dans le corps ;
//  - puces : `icon-list` du package avec une icône FIXE (chevron) — les données n'ont pas
//    d'icône par puce ;
//  - langues : `skill-levels` exige un POURCENTAGE et une image de drapeau par langue, que
//    les données n'ont pas (niveau = texte libre) : liste simple « langue — niveau » ;
//  - accroche : section « À propos » de la barre latérale (emplacement de l'exemple) ;
//  - liens : champs SANS schéma (cf. render.py::_split_links) -> on préfixe https://.

#import "@preview/lavandula:0.1.1": *

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── Réglages ────────────────────────────────────────────────────────────────────
#let options = data.at("options", default: (:))
// Option VISUELLE « couleur » : couples (primary, secondary) du package ; toutes les autres
// couleurs en dérivent. lavande = défaut du package.
#let palettes = (
  lavande: (primary: rgb("#414cc8"), secondary: rgb("#f7f4fc")),
  sauge: (primary: rgb("#3d7a5c"), secondary: rgb("#f1f6f2")),
  ardoise: (primary: rgb("#3b4a5a"), secondary: rgb("#f2f4f6")),
)
// Option VISUELLE « position_barre » : left | right (largeurs inchangées).
#let sidebar_position = options.at("position_barre", default: "left")
// Option de MISE EN PAGE « largeur_barre » : largeur de la barre latérale (36 % par défaut).
#let sidebar_widths = (etroite: 30%, normale: 36%, large: 40%)
#let sidebar_width = sidebar_widths.at(options.at("largeur_barre", default: "normale"))

#show: lavandula-theme.with(custom-colors: palettes.at(options.at("couleur", default: "lavande")))
#set text(lang: "fr")
#set document(title: data.profile.name + " (CV)", author: data.profile.name, date: none)

#let profile_field(key) = data.profile.at(key, default: "")
#let contacts = (
  ("email", "at", true, k => "mailto:" + k),
  ("phone", "phone", true, none),
  ("location", "location-dot", true, none),
  ("github", "github", false, k => "https://" + k),
  ("linkedin", "linkedin", false, k => "https://" + k),
  ("site", "globe", true, k => "https://" + k),
).filter(c => profile_field(c.at(0)) != "").map(((key, icon, solid, target)) => {
  let value = profile_field(key)
  (icon: icon, icon-solid: solid, text: if target == none { value } else { link(target(value), value) })
})

#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

// Pastilles de compétences SANS groupe/icône de tête (le libellé est le titre de section).
#let pills(items) = {
  set par(leading: 4.2pt)
  context for skill in items {
    box(
      fill: get-color("skill-box"),
      stroke: 1pt + get-color("skill-box").darken(8%),
      inset: 5.4pt,
      radius: 4pt,
      skill,
    )
    h(3.5pt)
  }
}

// Entrée : intitulé (+ dates à droite), organisation en italique, puces à icône.
// Puces : chacune est un champ éditable, identifié par son id de TRAÇABILITÉ.
// `section-element` place le titre dans une colonne `auto` : sans borne, un titre long prend
// toute la largeur et la date (colonne `1fr`) se casse sur 3 lignes. On borne donc le titre
// pour réserver la largeur d'une date « AAAA-MM – AAAA-MM » à 7 pt, et la date est insécable.
// (Dans une colonne `auto`, `100 %` ne vaut PAS la largeur de colonne : on la calcule.)
#let date_room = 70pt + 9pt  // date à 7 pt + gouttière du package
#let main_width = 595.28pt * (1 - sidebar_width / 100%) - 2 * 17pt  // A4 − barre − inset
#let bounded(title) = context {
  let max = main_width - date_room
  if measure(title).width > max { block(width: max, title) } else { title }
}
#let entry(it) = section-element(
  title: bounded(it.title),
  info: box(emph(it.dates)),
  [
    #emph(it.organization)
    #set text(size: sizes.text-s2)
    #if it.bullets.len() > 0 {
      icon-list(it.bullets.map(b => (icon: "chevron-right", icon-solid: true, text: field(b.id, b.text))))
    }
  ],
)

#cv(
  sidebar-position: sidebar_position,
  sidebar-width: sidebar_width,
  sidebar: [
    = #data.profile.name

    #contact-list(contacts)

    #if data.show_accroche and data.accroche != "" {
      sidebar-section(title: "À propos")[
        #set par(justify: true)
        #field("accroche", data.accroche)
      ]
    }

    #if data.hard_skills.len() > 0 {
      sidebar-section(title: "Compétences")[
        #field("hard_skills", data.hard_skills.join(", "), body: pills(data.hard_skills))
      ]
    }

    #if data.show_soft_skills and data.soft_skills.len() > 0 {
      sidebar-section(title: "Savoir-être")[
        #field("soft_skills", data.soft_skills.join(", "), body: pills(data.soft_skills))
      ]
    }

    #if data.languages.len() > 0 {
      sidebar-section(title: "Langues")[
        #for l in data.languages [
          #text(weight: "semibold", l.name) — #l.level \
        ]
      ]
    }

    #if data.interests.len() > 0 {
      sidebar-section(title: "Centres d'intérêt")[
        #field("interests", data.interests.join(", "), body: pills(data.interests))
      ]
    }
    // [mesure:fin-barre]
  ],
  main-content: [
    #if experiences.len() > 0 {
      section(title: "Expérience")[
        #for it in experiences { entry(it) }
      ]
    }

    #if projets.len() > 0 {
      section(title: "Projets")[
        #for it in projets { entry(it) }
      ]
    }

    #if data.education.len() > 0 {
      section(title: "Formation")[
        #for e in data.education {
          section-element(title: bounded(e.school), info: box(emph(e.dates)))[
            #set text(size: sizes.text-s2)
            #emph(e.degree)
          ]
        }
      ]
    }
    // [mesure:fin-principale]
  ],
)
