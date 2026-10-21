// Instrumentation d'ÉDITION : rend chaque zone retouchable repérable dans la page.
//
// `field(id, text)` ne change RIEN au rendu — `metadata` est un élément de taille
// nulle et `layout`/`measure` n'émettent aucun contenu. Il enregistre :
//   - le TEXTE effectivement rendu (donc déjà composé/assemblé par le template) ;
//   - la BOÎTE réellement occupée, en points PDF (origine = coin haut-gauche de page).
//
// Le front superpose ces boîtes à l'aperçu pour savoir quelle phrase est où : c'est le
// COMPILATEUR qui mesure et qui dit ce qu'il a écrit, jamais le front (aucun
// appariement de texte approximatif sur le PDF, aucune heuristique).
//
// Relevé côté Python (cf. core/rendering/typst_renderer.query_render_marks) :
//   typst eval 'query(<jtfield>).map(it => it.value)' --in <template> --format json
//
// `measure(text, width: size.width)` donne la taille du contenu REPLIÉ à la largeur
// disponible localement (celle de l'élément de liste pour une puce, celle de la page
// pour une ligne de compétences) : une puce sur deux lignes donne donc bien une boîte
// de deux lignes de haut, pleine largeur.
//
// `body` permet de rendre une version STYLÉE (ex. en italique) tout en enregistrant le
// texte brut ; la boîte est alors mesurée sur le rendu stylé, pas sur le texte nu.

#let field(id, text, body: none) = {
  let shown = if body == none { text } else { body }
  layout(size => context {
    let p = here().position()
    let m = measure(shown, width: size.width)
    [#metadata((
      id: id,
      text: text,
      page: p.page,
      x: p.x / 1pt,
      y: p.y / 1pt,
      w: m.width / 1pt,
      h: m.height / 1pt,
    ))<jtfield>]
    shown
  })
}
