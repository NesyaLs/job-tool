/* Aperçu du document : une image PNG par page, rendue par le serveur à partir de la MÊME
   compilation Typst que le PDF. Le navigateur n'interprète aucun PDF.

   En mode retouche, des zones cliquables sont posées par-dessus l'image, aux positions que
   le compilateur a mesurées, en POURCENTAGES de la page : elles restent alignées à toute
   largeur d'affichage, sans calcul. Les positions passent par des propriétés CSS
   personnalisées (--x, --y, --w, --h), lues par la feuille de style. */

import { el } from '../core/dom.js';

function Zone(field, session) {
  const s = session.state;
  const zone = el('button', {
    class: 'pdf-zone' + (field.id === s.selectedField ? ' is-selected' : '') + (field.edited ? ' is-edited' : ''),
    type: 'button',
    'aria-label': `Retoucher : ${field.text.slice(0, 80)}`,
    'aria-pressed': String(field.id === s.selectedField),
    onClick: () => session.selectField(field.id),
  });
  zone.style.setProperty('--x', `${field.left}%`);
  zone.style.setProperty('--y', `${field.top}%`);
  zone.style.setProperty('--w', `${field.width}%`);
  zone.style.setProperty('--h', `${field.height}%`);
  return zone;
}

function PageView(session, pageNumber, interactive) {
  const view = session.state.view;
  const image = el('img', { class: 'pdf-img', alt: `Page ${pageNumber} du document` });
  session.artifactUrl(view.preview_urls[pageNumber - 1])
    .then(url => { image.src = url; })
    .catch(() => { image.alt = `Page ${pageNumber} : aperçu indisponible`; });
  const zones = interactive ? view.fields.filter(f => f.page === pageNumber).map(f => Zone(f, session)) : [];
  return el('figure', { class: 'pdf-page' + (interactive ? ' is-interactive' : '') },
    image,
    ...zones,
    view.page_count > 1 && el('figcaption', { class: 'pdf-page-num' }, `${pageNumber} / ${view.page_count}`));
}

export function Pages(session, { interactive = false } = {}) {
  const view = session.state.view;
  const pages = [];
  for (let page = 1; page <= view.page_count; page++) pages.push(PageView(session, page, interactive));
  return el('div', { class: 'pdf-viewer' }, ...pages);
}
