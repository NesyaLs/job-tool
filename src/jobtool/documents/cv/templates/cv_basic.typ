// Template Typst « basic » : habillage du package @preview/basic-resume (version
// ÉPINGLÉE ci-dessous — la tenue sur 1 page est mesurée sur CE rendu précis).
// Mêmes données que cv_classic.typ / cv_compact.typ (render_input.json, mêmes clés) et
// mêmes zones éditables (field). Une seule colonne, texte extractible (ATS) : le package
// désactive les ligatures et n'utilise aucun tableau de mise en page.
//
// Choix assumés :
//  - marges du package conservées (0.5in, non paramétrables sans recopier `resume`) ;
//  - l'en-tête est composé par le package à partir des champs SÉPARÉS de `profile`
//    (email, phone, location, github, linkedin, site), déjà sans schéma http(s)://
//    (cf. cv/render.py::_split_links) : le package ajoute `https://` lui-même ;
//  - les sections absentes du package (accroche, compétences, langues, savoir-être,
//    intérêts) sont des titres de niveau 2 : elles héritent de SON style de section
//    (petites capitales + filet), aucun style parallèle.

#import "@preview/basic-resume:0.2.9": edu, resume, work

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── Réglages ────────────────────────────────────────────────────────────────────
#let list_separator = "  |  "   // séparateur des listes en ligne (celui du contact du package)
#let accroche_gap = 0.3em       // espace sous la phrase d'accroche

// Couleur d'accent (option VISUELLE « couleur » : aucun effet sur la mise en page). On passe
// par le paramètre `accent-color` du package, qui colore le nom, les titres de section et
// les liens ; texte, dates et filets restent noirs. Bleu = valeur de l'exemple du package.
#let accent_colors = (bleu: "#26428b", noir-blanc: "#000000")
// Options du template : absentes d'un brouillon enregistré avant leur ajout -> défauts.
#let options = data.at("options", default: (:))
#let accent_color = accent_colors.at(options.at("couleur", default: "bleu"))

// Lecture tolérante des champs séparés : un brouillon enregistré avant leur ajout ne
// les porte pas ; l'en-tête se réduit alors au nom plutôt que de faire échouer le rendu.
#let profile_field(key) = data.profile.at(key, default: "")

#show: resume.with(
  author: data.profile.name,
  email: profile_field("email"),
  phone: profile_field("phone"),
  location: profile_field("location"),
  github: profile_field("github"),
  linkedin: profile_field("linkedin"),
  personal-site: profile_field("site"),
  accent-color: accent_color,
  paper: "a4",
  lang: "fr",
)

// ── Phrase d'accroche (OPT-IN, pilotée par le profil du template) ───────────────
#if data.show_accroche and data.accroche != "" [
  #field("accroche", data.accroche, body: emph(data.accroche))
  #v(accroche_gap)
]

// ── Sections empilées verticalement (une seule colonne) ────────────────────────
#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

// Puces : chacune est un champ éditable, identifié par son id de TRAÇABILITÉ.
#let bullets(it) = if it.bullets.len() > 0 {
  list(..it.bullets.map(b => field(b.id, b.text)))
}

#if experiences.len() > 0 [
  == Expérience
  #for it in experiences [
    #work(title: it.title, company: it.organization, dates: it.dates)
    #bullets(it)
  ]
]

// Projet : même composant que l'expérience (titre + dates / organisation). `project()`
// du package tient sur UNE ligne sans repli : un intitulé long y colle les dates au
// texte (« …étudiants)2024-09 ») ; la forme sur deux lignes reste propre à toute longueur.
#if projets.len() > 0 [
  == Projets
  #for it in projets [
    #work(title: it.title, company: it.organization, dates: it.dates)
    #bullets(it)
  ]
]

// Formation : `consistent` place les dates en haut à droite, comme les expériences.
// `edu` émet deux lignes SANS fin de paragraphe (le package attend une liste derrière) :
// le parbreak explicite empêche deux formations de se fondre en un seul paragraphe.
#if data.education.len() > 0 [
  == Formation
  #for e in data.education {
    edu(institution: e.school, degree: e.degree, dates: e.dates, consistent: true)
    parbreak()
  }
]

#if data.hard_skills.len() > 0 [
  == Compétences techniques
  #field("hard_skills", data.hard_skills.join(", "))
]

#if data.languages.len() > 0 [
  == Langues
  #data.languages.map(l => l.name + " : " + l.level).join(list_separator)
]

// Soft skills : OPT-IN (baseline = pas de section, même si le CV en contient).
#if data.show_soft_skills and data.soft_skills.len() > 0 [
  == Savoir-être
  #field("soft_skills", data.soft_skills.join(", "))
]

#if data.interests.len() > 0 [
  == Centres d'intérêt
  #field("interests", data.interests.join(", "))
]
