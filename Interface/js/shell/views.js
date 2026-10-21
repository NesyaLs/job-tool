/* Registre des vues : quelle fonction remplit le contenu pour un espace et une section.

   Chaque espace s'inscrit au démarrage (app.js) ; le cadre ne connaît aucun écran. Une vue
   reçoit le conteneur `<main>` et le remplit ; elle renvoie éventuellement une fonction de
   nettoyage, appelée avant la vue suivante. */

import { el, fill } from '../core/dom.js';

const views = {};
let cleanup = null;

/* Vue d'un espace : elle reçoit la section ouverte (ex. le dossier d'Offres). */
export function registerSpace(space, mount) {
  views[space] = mount;
}

export function showView(main, { space, section }) {
  if (cleanup) { cleanup(); cleanup = null; }
  main.className = 'main';
  main.replaceChildren();
  const mount = views[space];
  if (!mount) {
    main.classList.add('is-single');
    fill(main, el('div', { class: 'pane-page' },
      el('div', { class: 'state state-center', dataset: { kind: 'error' } },
        el('p', { class: 'state-title' }, 'Section introuvable'),
        el('p', {}, 'Cette page n’existe pas ou plus. Revenez aux offres.'),
        el('a', { class: 'btn', href: '#/offres/nouvelles' }, 'Aller aux offres'))));
    return;
  }
  const result = mount(main, section);
  if (typeof result === 'function') cleanup = result;
}
