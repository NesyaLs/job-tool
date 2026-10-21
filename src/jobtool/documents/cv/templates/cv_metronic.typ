// Template Typst « metronic-photo » : VARIANTE de cv_metronic.typ avec photo de profil ronde
// en tête de la barre latérale. Tout le reste est IDENTIQUE à cv_metronic.typ ; sans photo
// dans les données, le rendu est celui de cv_metronic.typ au point près.
//
// Photo (le package metronic n'en prévoit AUCUNE : ni paramètre, ni composant) :
//  - donnée : `profile.photo` = chemin RELATIF au dossier de compilation (ex. "photo.jpg"),
//    absente, null ou "" = pas de photo. Le code de rendu y copie le fichier (même dossier que
//    render_input.json : Typst refuse tout chemin hors de la racine du projet) ;
//  - lue en OCTETS (`read(…, encoding: none)`) : Typst reconnaît alors le format au
//    CONTENU (PNG, JPEG, GIF, WebP, SVG, PDF) et non à l'extension du nom de fichier ;
//  - disque : bloc carré de côté `diametre`, rayon = diametre / 2, `clip: true` -> découpe
//    CIRCULAIRE ; image en `fit: "cover"` -> remplit le disque sans déformation, l'excédent
//    (haut/bas d'un portrait, côtés d'un paysage) est rogné, recadrage centré ;
//  - centrée dans la barre, au-dessus du nom.
//
// ---- Reste de l'en-tête d'origine (cv_metronic.typ) ----
//
// Template Typst « metronic » : habillage du package @preview/metronic (version ÉPINGLÉE
// ci-dessous : la tenue sur 1 page est mesurée sur CE rendu précis). Deux colonnes : barre
// latérale pleine hauteur en couleur d'accent (nom, accroche, contacts, compétences en
// pastilles, langues, intérêts) et colonne principale (expériences, projets, formation),
// titres de section précédés d'une icône Font Awesome.
// Mêmes données que cv_basic.typ (render_input.json, mêmes clés) et mêmes zones
// éditables (field).
//
// ⚠ Mise en page de page RÉÉCRITE (`page-layout` ci-dessous, ~15 lignes) au lieu de
// `resume-page` du package : celui-ci place les deux colonnes dans une grille à hauteur
// FIXE (`rows: (100%)`). Un contenu trop long y est TRONQUÉ EN SILENCE (texte perdu, PDF
// toujours d'1 page) : la vérification « 1 page » du projet ne verrait jamais le
// débordement. La réécriture garde les mêmes proportions (1.9fr / 3fr), les mêmes
// retraits (`render-area` du package) et la même détection de couleur de texte, mais en
// hauteur automatique : un débordement crée une 2e page, comme pour les autres templates.
// La barre latérale pleine hauteur est dessinée en fond de page.
//
// Autres choix assumés :
//  - police Inter en 12 pt imposée par le package (`render-area`, non paramétrable) : on la
//    garde telle quelle (option « densite » ci-dessous pour une variante resserrée) ;
//  - `contact` du package n'émet AUCUN lien (texte brut) : on lui passe des `link` déjà
//    construits (champs SANS schéma, cf. render.py::_split_links -> on préfixe https://) ;
//  - rôle sous le nom (`medium` dans l'exemple) : absent des données, omis ;
//  - langues : pas de composant dédié -> lignes « langue — niveau » en `small` ;
//  - `section` du package : titre `==` + icône, puis corps avec 15 pt de marge verticale ;
//  - formation déplacée dans la colonne principale (voir plus bas) : la barre latérale
//    déborde seule sinon, même sans aucune expérience.

#import "@preview/metronic:1.1.0": *

#let data = json("render_input.json")

// Zones retouchables à la main (post-traitement) : `field` enregistre leur boîte
// sans toucher au rendu. Voir _edit.typ.
#import "_edit.typ": field

// ── Réglages ────────────────────────────────────────────────────────────────────
#let options = data.at("options", default: (:))
// Option VISUELLE « couleur » : (accent, fond) passés à `theme` ; la couleur du texte de
// chaque colonne est déduite par le package (blanc sur fond sombre, noir sur fond clair).
#let palettes = (
  turquoise: (accent: rgb("61B7AE"), fond: rgb("F2F0EF")),   // défaut du package
  marine: (accent: rgb("1F3A5F"), fond: rgb("FFFFFF")),
  corail: (accent: rgb("E07A5F"), fond: rgb("FBF8F3")),
)
// Option de MISE EN PAGE « densite » (BRICOLAGE, non native) : `normale` = 12 pt imposés
// par le package ; `serree` = 10 pt (on redéfinit la taille dans chaque colonne).
#let text_sizes = (normale: 12pt, serree: 10pt)
// Option de MISE EN PAGE « taille_photo » : diamètre du disque (sans effet sans photo).
#let photo_sizes = (petite: 80pt, moyenne: 100pt, grande: 120pt)
#let photo_diameter = photo_sizes.at(options.at("taille_photo", default: "moyenne"))
#let palette = palettes.at(options.at("couleur", default: "turquoise"))
// Densité SERRÉE par défaut dans ce projet : à 12 pt (densité « normale » du package),
// 3 expériences + 3 projets avec une puce longue chacun DÉBORDENT déjà (mesuré). L'option
// n'est pas exposée côté Python : seule la valeur serrée est utilisée.
#let body_size = text_sizes.at(options.at("densite", default: "serree"))

#theme(accent-color: palette.accent, background-color: palette.fond)
#set text(lang: "fr")
#set document(title: data.profile.name, author: data.profile.name)

// Remplace `resume-page` (cf. en-tête) : mêmes proportions et retraits, hauteur automatique.
#let sidebar-ratio = 1.9 / (1.9 + 3)
#let page-layout(sidebar: none, main) = context {
  let accent = get-accent-color()
  let background = get-background-color()
  set page("a4", margin: 0pt, fill: background,
    background: place(top + left, rect(width: sidebar-ratio * 100%, height: 100%, fill: accent)))
  grid(
    columns: (1.9fr, 3fr),
    context {
      set-current-background-color(accent)
      render-area(detect-text-color(accent), { set text(size: body_size); sidebar })
    },
    context {
      set-current-background-color(background)
      render-area(detect-text-color(background), { set text(size: body_size); main })
    },
  )
}

#let profile_field(key) = data.profile.at(key, default: "")
#let as_link(key, prefix) = {
  let value = profile_field(key)
  if value == "" { "" } else { link(prefix + value, value) }
}

#let experiences = data.experiences.filter(it => it.type in ("internship", "job"))
#let projets = data.experiences.filter(it => it.type in ("school_project", "personal_project"))

// Entrée : `===` intitulé, ligne « organisation — dates », puces.
// Puces : chacune est un champ éditable, identifié par son id de TRAÇABILITÉ.
#let entry(it) = [
  === #it.title
  #small[#it.organization — #it.dates]

  #list(..it.bullets.map(b => field(b.id, b.text)))
  #v(6pt)
]

// Photo ronde : disque découpé (clip) dans lequel l'image remplit tout en « cover ».
// Anneau de 2,5 pt dans la couleur du TEXTE de la barre (blanc sur accent sombre, noir sur
// accent clair, déduite par le package) : détache la photo du fond quand son arrière-plan
// est proche de l'accent, et reprend la couleur des textes voisins.
#let photo_path = data.profile.at("photo", default: none)  // absente, null ou "" = sans photo
#let has_photo = photo_path not in (none, "")
#let photo-disc(path, diameter) = context block(
  width: diameter,
  height: diameter,
  radius: diameter / 2,
  clip: true,
  stroke: 2.5pt + detect-text-color(get-accent-color()),
  image(read(path, encoding: none), width: 100%, height: 100%, fit: "cover"),
)

// Contenu de la barre SOUS la photo : identique à cv_metronic.typ.
#let sidebar_body = [
    = #data.profile.name

    #if data.show_accroche and data.accroche != "" [
      #v(5pt)
      #field("accroche", data.accroche)
    ]

    #v(5pt)

    #small(contact(
      email: as_link("email", "mailto:"),
      phone: profile_field("phone"),
      location: profile_field("location"),
      github: as_link("github", "https://"),
      linkedin: as_link("linkedin", "https://"),
      website: as_link("site", "https://"),
    ))

    #let show_soft = data.show_soft_skills and data.soft_skills.len() > 0
    #if data.hard_skills.len() > 0 or show_soft {
      section(icon: "check-double", "Compétences")[
        #if data.hard_skills.len() > 0 [
          #field("hard_skills", data.hard_skills.join(", "), body: tags(..data.hard_skills))
        ]
        #if show_soft [
          === Savoir-être
          #v(5pt)
          #field("soft_skills", data.soft_skills.join(", "), body: tags(..data.soft_skills))
        ]
      ]
    }

    #if data.languages.len() > 0 {
      section(icon: "language", "Langues")[
        #small[
          #for l in data.languages [
            #strong(l.name) — #l.level \
          ]
        ]
      ]
    }

    #if data.interests.len() > 0 {
      section(icon: "heart", "Centres d'intérêt")[
        #field("interests", data.interests.join(", "), body: tags(..data.interests))
      ]
    }
    // [mesure:fin-barre]
]

// Photo AJUSTÉE à la place libre : la barre ne doit jamais déborder à cause d'elle.
// Place libre = hauteur utile de la barre (page − 2 × 20 pt de `render-area`) − hauteur du
// reste de la barre mesurée à SA largeur − espacement qui apparaît au-dessus du nom quand
// un bloc le précède (mesuré, pas supposé : ≈ 18 pt à 10 pt). Diamètre = option
// `taille_photo`, réduit si besoin (avertissement <jtwarning>) ; sous `photo_min`, photo
// omise (avertissement). Données de base : aucune réduction jusqu'à la grande taille.
#let photo_min = 56pt  // ≈ 2 cm : en dessous, un visage n'est plus reconnaissable
#let sidebar_height = 842pt - 2 * 20pt  // A4 − pad vertical de `render-area`
#let warn(message) = [#metadata(message)<jtwarning>]
// Largeur utile de la barre : colonne 1.9 fr sur 4.9 fr d'une page A4 sans marge, moins le
// pad gauche/droite de `render-area` (20 pt + 14 pt). Calculée plutôt que lue par `layout` :
// `layout` est un élément BLOC, qui ferait apparaître l'espacement au-dessus du nom même
// quand la photo est omise (la barre gagnerait ≈ 18 pt et pourrait déborder).
#let sidebar_width = 210mm * sidebar-ratio - (20pt + 14pt)
#let fitted-photo(path, target, body) = context {
  let with_gap = measure(block(width: sidebar_width, [#block(height: 0pt, below: 0pt) #body])).height
  let free = sidebar_height - with_gap - 1pt  // 1 pt de garde (arrondis de mise en page)
  let diameter = calc.min(target, free)
  if diameter < photo_min {
    warn("photo omise : " + str(calc.round(free / 1pt)) + " pt libres dans la barre latérale, "
      + str(photo_min / 1pt) + " pt au minimum")
  } else {
    if diameter < target {
      warn("photo réduite à " + str(calc.round(diameter / 1pt)) + " pt (demandé : "
        + str(target / 1pt) + " pt) faute de place dans la barre latérale")
    }
    // Espace sous la photo : celui, natif, du titre `=` du nom (qui l'emporte sur tout
    // espacement inférieur) -> même rythme que le reste de la barre.
    // Nom laissé aligné à GAUCHE comme toute la barre (accroche, contacts, titres) : la
    // photo est un emblème centré, le texte garde un seul axe.
    align(center, block(below: 0pt, photo-disc(path, diameter)))
  }
}

#show: page-layout.with(
  sidebar: [
    #if has_photo { fitted-photo(photo_path, photo_diameter, sidebar_body) }
    #sidebar_body
  ],
)

#if experiences.len() > 0 {
  section(icon: "briefcase", "Expérience")[
    #for it in experiences { entry(it) }
  ]
}

#if projets.len() > 0 {
  section(icon: "code", "Projets")[
    #for it in projets { entry(it) }
  ]
}
// Formation dans la colonne PRINCIPALE (l'exemple du package la met dans la barre) : avec
// toutes les sections exigées, la barre latérale déborde seule à 12 pt comme à 10 pt.
#if data.education.len() > 0 {
  section(icon: "graduation-cap", "Formation")[
    #for e in data.education [
      === #e.degree
      #small[#e.school — #e.dates]
      #v(4pt)
    ]
  ]
}
// [mesure:fin-principale]
