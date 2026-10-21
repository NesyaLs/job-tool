// Template « Moderne » d'une lettre de motivation française, sur le package
// @preview/modernpro-coverletter (licence MIT, téléchargé par Typst à la première
// compilation puis mis en cache).
//
// Même CONTRAT que lettre.typ (cf. cover_letter/registry.py) : mêmes données
// (render_input.json), mêmes ids `field()`, mêmes inserts concaténés au paragraphe, et
// nom / entreprise / date imprimés d'un seul tenant pour le contrôle ATS.
//
// Deux mesures ne peuvent être faites que par le compilateur ; il les signale par un
// `<jtwarning>` (relevé côté Python, affiché sans rien couper) :
//   - l'en-tête du package a une hauteur FIXE : un titre sur deux lignes chevauche
//     l'adresse ;
//   - plus de 4 coordonnées débordent de la colonne de droite.

#import "@preview/modernpro-coverletter:1.0.2": coverletter
#import "_edit.typ": field

#let data = json("render_input.json")

// Une chaîne JSON s'affiche telle quelle : pas d'apostrophe typographique automatique
// comme dans le balisage. On la pose ici, à l'AFFICHAGE seulement : le texte enregistré
// par `field` reste celui des données (le contrôle ATS normalise les apostrophes).
#let typo(s) = s.replace("'", "’")

#let filled(s) = s.trim() != ""

// Avertissement mesuré par le compilateur, sans effet sur le rendu.
#let warn(message) = [#metadata(message)<jtwarning>]

// Lien cliquable déduit du texte d'une coordonnée : e-mail, téléphone ou adresse web.
#let contact-link(c) = {
  let t = c.trim()
  if t.starts-with("http://") or t.starts-with("https://") {
    t
  } else if t.contains("@") and not t.contains("/") {
    "mailto:" + t
  } else if t.match(regex("^\+?[0-9][0-9 .()-]{5,}$")) != none {
    "tel:" + t.replace(regex("[^0-9+]"), "")
  } else if t.contains(".") and not t.contains(" ") {
    "https://" + t
  } else {
    none
  }
}

// Affiché sans schéma, comme on l'écrit à la main (« github.com/… »).
#let contact-text(c) = typo(c.trim().replace(regex("^(https?://)?(www\.)?"), ""))

#let coordonnees = data.coordonnees.filter(filled)

// Le titre est placé dans une cellule de largeur fixe : on compare sa hauteur repliée à
// celle d'une ligne pour savoir s'il passe à la ligne.
#let titre = if filled(data.titre) {
  let body = typo(data.titre)
  layout(size => context {
    let one = measure(body)
    let wrapped = measure(body, width: size.width)
    if wrapped.height > one.height * 1.5 {
      warn("titre sur plusieurs lignes : il chevauche l'adresse, raccourcissez-le")
    }
    body
  })
} else { none }

#let insert_vous = if data.show_interet and data.insert_interet != "" {
  " " + data.insert_interet
} else { "" }
#let insert_moi = if data.show_projet and data.insert_projet != "" {
  " " + data.insert_projet
} else { "" }

#let paragraphe(id, s) = field(id, s, body: typo(s))

#set document(
  title: "Lettre de motivation, " + data.entreprise,
  author: data.nom,
)
#set text(lang: "fr", region: "FR")

#if coordonnees.len() > 4 {
  warn(
    str(coordonnees.len())
      + " coordonnées : l'en-tête n'en affiche proprement que 4, elles se chevauchent",
  )
}

#coverletter(
  profile: (
    name: typo(data.nom),
    role: titre,
    address: if filled(data.lieu) { typo(data.lieu) } else { none },
    contacts: coordonnees.map(c => {
      let l = contact-link(c)
      (text: contact-text(c)) + if l != none { (link: l) } else { (:) }
    }),
  ),
  theme: (font: "Libertinus Serif"),
  // Resserré par rapport aux réglages du package (paragraphes 1,55em, interligne
  // 0,85em), qui passent à deux pages aux plafonds de longueur de la passe 2 avec un
  // objet et un destinataire longs. Mesuré : ce réglage tient sur une page dans ce cas.
  layout: (paragraph-spacing: 1.2em, line-spacing: 0.8em),
  recipient: (
    name: if filled(data.destinataire) { typo(data.destinataire) } else { none },
    organization: typo(data.entreprise),
    date: data.date,
    subject: if data.objet != "" [Objet : #paragraphe("objet", data.objet)] else { none },
    greeting: if filled(data.salutation) { typo(data.salutation) } else { none },
  ),
  // Sans `salutation: none`, le package ajoute « Sincerely, ». Pas de `supplements` :
  // aucune mention de pièce jointe.
  closing: (salutation: none),
)[
  #paragraphe("paragraphe_vous", data.paragraphe_vous + insert_vous)

  #paragraphe("paragraphe_moi", data.paragraphe_moi + insert_moi)

  #paragraphe("paragraphe_nous", data.paragraphe_nous)

  #paragraphe("formule", data.formule)
]
