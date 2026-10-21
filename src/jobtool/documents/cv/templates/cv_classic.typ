// Template Typst FIXE, soigné mais 100% extractible (ATS).
// CONTRAINTE DURE : UNE SEULE COLONNE de contenu sur toute la hauteur — aucun
// tableau de mise en page, aucun bloc côte à côte. L'alignement des dates à droite
// se fait par espace flexible #h(1fr), pas par colonnes. Les filets de section sont
// des traits dessinés (#line), pas des caractères. Données depuis un JSON externe ;
// aucune donnée en dur.

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── RÉGLAGES DE MISE EN PAGE ───────────────────────────────────────────────────
// Tous les réglages d'espacement et de taille sont ici. Pour ajuster la densité,
// on change UNE valeur nommée ; aucune valeur magique n'est disséminée ailleurs.
#let page_margin = (x: 1.6cm, y: 0.8cm)   // marges de la page
#let body_size = 9.5pt                     // taille du texte courant (mini lisible)
#let body_leading = 0.38em                 // interligne à l'intérieur d'un paragraphe
#let contact_size = 8.5pt                  // taille du bloc contact + liens (en-tête)
#let org_size = 9pt                        // taille de l'organisation (légèrement réduite)
#let rule_stroke = 0.6pt                   // épaisseur du filet horizontal de section

#let header_name_size = 16pt               // taille du nom en haut
#let header_gap_after_name = 0.1em         // espace nom → bloc contact (resserré)
#let header_gap_after_contact = 0.35em     // espace bloc contact → 1ʳᵉ section

#let section_title_size = 10.5pt           // taille d'un intitulé de section
#let section_gap_before = 0.22em           // espace AU-DESSUS d'un titre de section
#let section_gap_title_to_rule = 0.05em    // espace titre → filet (filet collé au titre)
#let section_gap_after_rule = 0.18em       // espace filet → 1ʳᵉ entrée

#let entry_gap = 0.16em                     // espace entre deux entrées
#let bullet_gap = 0.18em                    // espace entre deux puces

// ── Application des réglages globaux ────────────────────────────────────────────
#set page(margin: page_margin)
#set text(size: body_size)
#set par(justify: false, leading: body_leading)
#set list(tight: false, spacing: bullet_gap)

// Titre de section : libellé en majuscules gras + filet horizontal pleine largeur.
#let section(title) = [
  #v(section_gap_before)
  #text(weight: "bold", size: section_title_size)[#upper(title)]
  #v(section_gap_title_to_rule)
  #line(length: 100%, stroke: rule_stroke)
  #v(section_gap_after_rule)
]

// Une entrée expérience / projet. Première ligne : titre (gras) — organisation
// (italique, légèrement réduite), dates à droite via #h(1fr).
// RÈGLE DÉTERMINISTE de débordement : on MESURE la largeur naturelle de
// « titre — organisation (+gap mini) dates ». Si elle tient dans la largeur
// disponible, tout reste sur une ligne. Sinon, on place titre + dates sur la 1ʳᵉ
// ligne et l'organisation seule sur une 2ᵉ ligne dédiée (jamais de coupure sauvage).
#let entry(it) = {
  let head_title = strong[#it.title]
  let head_org = text(size: org_size, style: "italic")[#it.organization]
  let head_dates = [#it.dates]
  layout(bounds => {
    let natural = measure(box[#head_title — #head_org #h(1em) #head_dates]).width
    if natural <= bounds.width {
      [#head_title — #head_org #h(1fr) #head_dates]
    } else {
      [#head_title #h(1fr) #head_dates \ #head_org]
    }
  })
  // Chaque puce est un champ éditable, identifié par son id de TRAÇABILITÉ.
  if it.bullets.len() > 0 { list(..it.bullets.map(b => field(b.id, b.text))) }
  v(entry_gap)
}

// ── En-tête : nom en grand, puis contact et liens en texte simple ──────────────
#align(center)[
  #text(size: header_name_size, weight: "bold")[#data.profile.name]
  #v(header_gap_after_name)
  #text(size: contact_size)[#data.profile.contact]
  #if data.profile.links.len() > 0 [
    \ #text(size: contact_size)[#data.profile.links.join("  ·  ")]
  ]
]
#v(header_gap_after_contact)

// ── Sections empilées verticalement (une seule colonne) ────────────────────────
#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

#if experiences.len() > 0 [
  #section("Expérience")
  #for it in experiences [ #entry(it) ]
]

#if projets.len() > 0 [
  #section("Projets")
  #for it in projets [ #entry(it) ]
]

#if data.education.len() > 0 [
  #section("Formation")
  #for edu in data.education [
    // Ligne 1 : intitulé seul (gras). Ligne 2 : établissement (discret) + dates à droite.
    #strong[#edu.degree] \
    #text(size: org_size)[#emph[#edu.school] #h(1fr) #edu.dates]
    #v(entry_gap)
  ]
]

#if data.hard_skills.len() > 0 [
  #section("Compétences techniques")
  #field("hard_skills", data.hard_skills.join(", "))
]

#if data.languages.len() > 0 [
  #section("Langues")
  #data.languages.map(l => l.name + " : " + l.level).join("  ·  ")
]

#if data.soft_skills.len() > 0 [
  #section("Savoir-être")
  #field("soft_skills", data.soft_skills.join(", "))
]

#if data.interests.len() > 0 [
  #section("Centres d'intérêt")
  #field("interests", data.interests.join(", "))
]
