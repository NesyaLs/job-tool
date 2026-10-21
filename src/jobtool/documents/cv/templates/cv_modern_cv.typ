// Template Typst « modern-cv » : habillage du package @preview/modern-cv (version ÉPINGLÉE
// ci-dessous : la tenue sur 1 page est mesurée sur CE rendu précis). Port d'Awesome-CV :
// une colonne, titres de section colorés suivis d'un filet, en-tête centré.
// Mêmes données que cv_basic.typ (render_input.json, mêmes clés) et mêmes zones
// éditables (field).
//
// Choix assumés :
//  - `profile-picture: none` OBLIGATOIRE : la valeur par défaut du package est la FONCTION
//    `image` (pas `none`), ce qui ferait échouer le rendu. Aucune photo dans les données ;
//  - `date: ""` et pied de page désactivé par défaut : la date par défaut du package est
//    celle du JOUR, en anglais (rendu non déterministe) ;
//  - liens github/linkedin/site passés en `custom` (URL complète, on préfixe `https://`) :
//    les champs natifs `github`/`linkedin` attendent un NOM D'UTILISATEUR et préfixent
//    eux-mêmes l'URL (github.com/<x>, linkedin.com/in/<x>) ; `website`/`homepage` ne
//    préfixent RIEN (lien sans schéma = lien cassé). Nos champs sont déjà SANS schéma
//    (cf. render.py::_split_links) ;
//  - `positions` (sous-titre sous le nom) est REQUIS par le package : les données n'ont pas
//    de titre de poste, on passe une liste vide (ligne vide, rien d'affiché) ;
//  - le nom est coupé au PREMIER espace : prénom (fin, couleur d'accent) / nom (gras) ;
//  - sections absentes du package (accroche, projets, langues, savoir-être, intérêts) :
//    accroche = paragraphe en italique sous l'en-tête ; projets = mêmes `resume-entry` que
//    les expériences ; langues / savoir-être / intérêts = lignes de `resume-skill-item`
//    dans la section « Compétences » (grille libellé / valeurs du package).

#import "@preview/modern-cv:0.10.0": *

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── Réglages ────────────────────────────────────────────────────────────────────
// Options du template : absentes d'un brouillon enregistré avant leur ajout -> défauts.
#let options = data.at("options", default: (:))
// Option VISUELLE « couleur » (nom + titres de section) : aucun effet sur la mise en page.
// bleu = couleur par défaut du package (#262F99).
#let accent_colors = (bleu: "#262F99", bordeaux: "#8B1E3F", vert: "#1E6B52", noir: "#333333")
#let accent_color = rgb(accent_colors.at(options.at("couleur", default: "bleu")))
// Option de MISE EN PAGE « pied_de_page » : le package réserve alors 20 mm en bas au lieu
// de 10 mm (nom + « Curriculum Vitae » + numéro de page).
#let show_footer = options.at("pied_de_page", default: false)
// Option VISUELLE « petites_capitales » : sous-titres d'entrée en petites capitales.
#let use_smallcaps = options.at("petites_capitales", default: true)

#let profile_field(key) = data.profile.at(key, default: "")
#let name_parts = data.profile.name.split(" ")
#let firstname = name_parts.first()
#let lastname = name_parts.slice(1).join(" ", default: "")

// Liens : champs SANS schéma -> on affiche tel quel, on lie avec https://.
#let link_items = (
  ("github", "github"),
  ("linkedin", "linkedin"),
  ("site", "globe"),
).filter(((key, icon)) => profile_field(key) != "").map(((key, icon)) => (
  text: profile_field(key),
  icon: icon,
  link: "https://" + profile_field(key),
))

#let author = (
  firstname: firstname,
  lastname: lastname,
  positions: (),
  custom: link_items,
)
#if profile_field("email") != "" { author.insert("email", profile_field("email")) }
#if profile_field("phone") != "" { author.insert("phone", profile_field("phone")) }
#if profile_field("location") != "" { author.insert("address", profile_field("location")) }

#show: resume.with(
  author: author,
  profile-picture: none,
  date: "",
  accent-color: accent_color,
  colored-headers: true,
  show-footer: show_footer,
  language: "fr",
  // Police nommée explicitement : la liste par défaut du package ("Source Sans 3",
  // "Source Sans Pro") avertit à chaque compilation quand la 2e police est absente.
  font: "Source Sans 3",
  paper-size: "a4",
  use-smallcaps: use_smallcaps,
  show-address-icon: true,
  keywords: (),
)

// ── Phrase d'accroche (OPT-IN, pilotée par le profil du template) ───────────────
#if data.show_accroche and data.accroche != "" [
  #v(0.4em)
  #align(center, text(size: 10pt, field("accroche", data.accroche, body: emph(data.accroche))))
]

// ── Sections (une seule colonne) ──────────────────────────────────────────────
#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

// Puces : chacune est un champ éditable, identifié par son id de TRAÇABILITÉ.
#let bullets(it) = if it.bullets.len() > 0 {
  resume-item(list(..it.bullets.map(b => field(b.id, b.text))))
}

// Entrée : intitulé en gras (ligne 1), organisation en petites capitales + dates à droite
// (ligne 2). `accent-color` doit être repassé à CHAQUE entrée (sinon couleur par défaut).
#let entry(it) = resume-entry(
  title: it.title,
  location: "",
  description: it.organization,
  date: it.dates,
  accent-color: accent_color,
)

#if experiences.len() > 0 [
  = Expérience
  #for it in experiences [
    #entry(it)
    #bullets(it)
  ]
]

#if projets.len() > 0 [
  = Projets
  #for it in projets [
    #entry(it)
    #bullets(it)
  ]
]

#if data.education.len() > 0 [
  = Formation
  #for e in data.education [
    #resume-entry(title: e.degree, location: "", description: e.school, date: e.dates,
      accent-color: accent_color)
  ]
]

// Compétences : grille « libellé | valeurs » du package, une ligne par rubrique.
#let skill_rows = ()
#if data.hard_skills.len() > 0 {
  skill_rows.push(("Techniques", field("hard_skills", data.hard_skills.join(", "))))
}
#if data.show_soft_skills and data.soft_skills.len() > 0 {
  skill_rows.push(("Savoir-être", field("soft_skills", data.soft_skills.join(", "))))
}
#if data.languages.len() > 0 {
  skill_rows.push(("Langues", data.languages.map(l => l.name + " (" + l.level + ")").join(", ")))
}
#if data.interests.len() > 0 {
  skill_rows.push(("Centres d'intérêt", field("interests", data.interests.join(", "))))
}
#if skill_rows.len() > 0 [
  = Compétences
  // Le package justifie tous les paragraphes : sur ces listes courtes séparées par des
  // virgules, la justification étire les espaces de façon visible -> alignement à gauche.
  #set par(justify: false)
  #for (label, value) in skill_rows {
    resume-skill-item(label, (value,))
  }
]
// [mesure:fin-principale]
