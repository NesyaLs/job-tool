// Template Typst FIXE, variante COMPACTE — 100% extractible (ATS).
// Identique à cv_classic.typ (mêmes réglages, sections, contraintes dures : une
// colonne, filets dessinés, données depuis render_input.json), SAUF le helper
// `entry` : règle FIXE sans layout ni measure (compilation instantanée et stable).

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── RÉGLAGES DE MISE EN PAGE ───────────────────────────────────────────────────
// Tous les réglages d'espacement et de taille sont ici. Pour ajuster la densité,
// on change UNE valeur nommée ; aucune valeur magique n'est disséminée ailleurs.
#let page_margin = (x: 1.6cm, y: 0.6cm)   // marges de la page (densifié)
#let body_size = 9.5pt                     // taille du texte courant (mini lisible)
#let body_leading = 0.38em                 // interligne à l'intérieur d'un paragraphe
#let contact_size = 8.5pt                  // taille du bloc contact + liens (en-tête)
#let org_size = 9pt                        // taille de l'organisation (légèrement réduite)
#let rule_stroke = 0.6pt                   // épaisseur du filet horizontal de section

#let header_name_size = 16pt               // taille du nom en haut
#let header_gap_after_name = 0.1em         // espace nom → bloc contact (resserré)
#let header_gap_after_contact = 0.25em     // espace bloc contact → 1ʳᵉ section (densifié)

#let section_title_size = 10.5pt           // taille d'un intitulé de section
#let section_gap_before = 0.14em           // espace AU-DESSUS d'un titre de section (densifié)
#let section_gap_title_to_rule = 0.05em    // espace titre → filet (filet collé au titre)
#let section_gap_after_rule = 0.12em       // espace filet → 1ʳᵉ entrée (densifié)

#let entry_gap = 0.1em                      // espace entre deux entrées (densifié)
#let bullet_gap = 0.3em                     // espace entre deux puces

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

// Une entrée expérience / projet — entête COMPACT, sans layout ni measure :
//   titre (gras) — organisation (italique, org_size) sur la MÊME ligne, dates
//   poussées à droite via #h(1fr). Si trop long, c'est le retour à la ligne
//   NATUREL de Typst qui s'applique (vise 1 ligne, tolère 2). Aucune branche.
#let entry(it) = {
  [#strong[#it.title] — #text(size: org_size, style: "italic")[#it.organization] #h(1fr) #it.dates]
  // Chaque puce est un champ éditable, identifié par son id de TRAÇABILITÉ
  // (l'achievement dont elle est la reformulation) : l'id d'édition et l'id de
  // provenance sont le même, on ne crée pas un second système d'identifiants.
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

// ── Phrase d'accroche (OPT-IN) : affichée seulement si le template l'active et que
// le texte résolu est non vide. Sinon ce bloc ne produit RIEN (baseline inchangée). ──
#if data.show_accroche and data.accroche != "" [
  #align(center)[#field("accroche", data.accroche, body: emph(data.accroche))]
  #v(header_gap_after_contact)
]

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

// Soft skills : OPT-IN. Affichés seulement si le template active show_soft_skills
// (baseline = pas de section, même quand le CV contient des soft skills).
#if data.show_soft_skills and data.soft_skills.len() > 0 [
  #section("Savoir-être")
  #field("soft_skills", data.soft_skills.join(", "))
]

#if data.interests.len() > 0 [
  #section("Centres d'intérêt")
  #field("interests", data.interests.join(", "))
]
